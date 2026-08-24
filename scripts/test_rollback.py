"""
Time-Travel 回滚逻辑验证脚本（临时表，不污染真实业务数据）

覆盖场景：
- DELETE 恢复（before_data 快照重新 INSERT）
- UPDATE 还原（按主键改回旧值）
- INSERT 撤销（解析 SQL 主键值 DELETE）
- LIFO 逆序回滚（回滚目标操作及其之后所有操作）
"""

import asyncio

from sqlalchemy import text

from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.repositories.mysql.meta.write_audit_log_repository import (
    WriteAuditLogRepository,
)
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.services.rollback_service import RollbackService

TAB = "test_rollback"
SID = "test-session-rollback"


async def main():
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()

    async with (
        meta_mysql_client_manager.session_factory() as meta_session,
        dw_mysql_client_manager.session_factory() as dw_session,
    ):
        dw = DWMySQLRepository(dw_session)
        audit = WriteAuditLogRepository(meta_session)
        service = RollbackService(audit, dw)

        # ---- 0. 清理历史测试数据 ----
        await dw_session.execute(text(f"DROP TABLE IF EXISTS `{TAB}`"))
        await dw_session.commit()
        await meta_session.execute(
            text("DELETE FROM write_audit_log WHERE session_id = :s"), {"s": SID}
        )
        await meta_session.commit()

        # ---- 1. 造初始数据 ----
        await dw_session.execute(
            text(
                f"CREATE TABLE `{TAB}` (player_id VARCHAR(16) PRIMARY KEY, name VARCHAR(32), gender VARCHAR(8))"
            )
        )
        await dw_session.execute(
            text(f"INSERT INTO `{TAB}` VALUES ('C001','alice','F'),('C002','bob','M'),('C003','cat','M')")
        )
        await dw_session.commit()
        print("[setup] 初始数据:", await dw.fetch_table_data(TAB))

        # ---- 2. 模拟三个写操作并落审计日志 ----
        # op1: DELETE C001
        await dw_session.execute(text(f"DELETE FROM `{TAB}` WHERE player_id='C001'"))
        await dw_session.commit()
        log1 = await audit.insert_audit_log(
            session_id=SID, seq=1, op_type="DELETE", table_name=TAB,
            sql_text=f"DELETE FROM `{TAB}` WHERE player_id='C001'",
            before_data=[{"player_id": "C001", "name": "alice", "gender": "F"}],
            row_count=1,
        )
        # op2: UPDATE C002 -> 新值
        await dw_session.execute(text(f"UPDATE `{TAB}` SET name='bobby' WHERE player_id='C002'"))
        await dw_session.commit()
        log2 = await audit.insert_audit_log(
            session_id=SID, seq=2, op_type="UPDATE", table_name=TAB,
            sql_text=f"UPDATE `{TAB}` SET name='bobby' WHERE player_id='C002'",
            before_data=[{"player_id": "C002", "name": "bob", "gender": "M"}],
            row_count=1,
        )
        # op3: INSERT C004
        await dw_session.execute(
            text(f"INSERT INTO `{TAB}` (player_id, name, gender) VALUES ('C004','dave','M')")
        )
        await dw_session.commit()
        log3 = await audit.insert_audit_log(
            session_id=SID, seq=3, op_type="INSERT", table_name=TAB,
            sql_text=f"INSERT INTO `{TAB}` (player_id, name, gender) VALUES ('C004','dave','M')",
            before_data=[], row_count=1,
        )
        print("[ops] 三个写操作已执行并落库审计:", log1, log2, log3)
        print("[ops] 当前数据:", await dw.fetch_table_data(TAB))

        # ---- 3. 回滚 op1（应逆序还原 op3、op2、op1）----
        result = await service.rollback(log1)
        print("[rollback] 摘要:", result)

        # ---- 4. 验证数据回到初始状态 ----
        final_rows = await dw.fetch_table_data(TAB)
        print("[verify] 回滚后数据:", final_rows)
        expected = {
            ("C001", "alice", "F"),
            ("C002", "bob", "M"),
            ("C003", "cat", "M"),
        }
        actual = {(r["player_id"], r["name"], r["gender"]) for r in final_rows}
        assert actual == expected, f"回滚后数据不一致: {actual}"
        print("[verify] PASS: 数据完全恢复")

        # ---- 5. 验证状态标记 ----
        logs = await audit.list_audit_logs(SID)
        statuses = [(l.log_id, l.status) for l in logs]
        assert all(s == "rolled_back" for _, s in statuses), statuses
        print("[verify] PASS: 审计状态全部标记 rolled_back:", statuses)

        # ---- 6. 重复回滚应报错 ----
        try:
            await service.rollback(log1)
            print("[verify] FAIL: 重复回滚未报错")
        except ValueError as e:
            print("[verify] PASS: 重复回滚被拒绝:", e)

        # ---- 7. 清理 ----
        await dw_session.execute(text(f"DROP TABLE IF EXISTS `{TAB}`"))
        await dw_session.commit()
        await meta_session.execute(
            text("DELETE FROM write_audit_log WHERE session_id = :s"), {"s": SID}
        )
        await meta_session.commit()
        print("[cleanup] 测试数据已清理")

    await meta_mysql_client_manager.close()
    await dw_mysql_client_manager.close()
    print("ALL DONE")


if __name__ == "__main__":
    asyncio.run(main())
