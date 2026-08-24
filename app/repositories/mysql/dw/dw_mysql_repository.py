"""
数仓 MySQL 仓储

这一层对应文档里的 DW Repository，职责是到真实数仓中补齐配置文件里
没有显式维护的信息，例如字段类型和字段示例值。Service 层只关心
“需要哪些信息”，具体怎样查数仓由仓储层统一封装
SQL 生成闭环中的数据库环境读取 SQL 校验和最终查询执行也集中放在这里
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DWMySQLRepository:
    """负责查询数仓真实表结构和字段样例值"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_column_types(self, table_name: str) -> dict[str, str]:
        """查询整张表的字段类型，作为 ColumnInfo.type 的真实来源"""
        sql = f"show columns from {table_name}"
        result = await self.session.execute(text(sql))
        result_dict = result.mappings().fetchall()
        return {row["Field"]: row["Type"] for row in result_dict}

    async def get_column_values(
        self, table_name: str, column_name: str, limit: int = 10
    ) -> list:
        """抽样查询字段示例值，供元数据入库和后续检索链路复用"""
        sql = f"select distinct {column_name} from {table_name} limit {limit}"
        result = await self.session.execute(text(sql))
        return [row[0] for row in result.fetchall()]

    async def get_db_info(self):
        """读取当前数仓数据库的方言和版本，供 SQL 生成提示词使用"""

        sql = "select version()"
        result = await self.session.execute(text(sql))
        version = result.scalar()

        # dialect 来自 SQLAlchemy 当前绑定的数据库方言，例如 mysql
        dialect = self.session.bind.dialect.name
        return {"dialect": dialect, "version": version}

    async def validate(self, sql: str):
        """用 EXPLAIN 让数据库提前解析 SQL，发现语法 表名 字段名等错误"""
        sql = f"explain {sql}"
        await self.session.execute(text(sql))

    async def run(self, sql: str) -> list[dict]:
        """执行最终 SQL，并把 SQLAlchemy 行对象转换成前端更易消费的字典列表"""
        result = await self.session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]

    async def list_tables(self) -> list[str]:
        """返回数仓中全部表名（SHOW TABLES，权限比 information_schema 更宽松）"""
        result = await self.session.execute(text("SHOW TABLES"))
        return [row[0] for row in result.fetchall()]

    async def get_primary_keys(self, table_name: str) -> list[str]:
        """查询表的主键列名（复合主键返回多列，按 Key_name='PRIMARY' 过滤）"""
        sql = f"SHOW KEYS FROM `{table_name}` WHERE Key_name = 'PRIMARY'"
        result = await self.session.execute(text(sql))
        return [row["Column_name"] for row in result.mappings().fetchall()]

    async def fetch_table_data(self, table_name: str) -> list[dict]:
        """查询单张表的全部数据，返回字典行列表（表名来自 SHOW TABLES，反引号防注入）"""
        sql = f"SELECT * FROM `{table_name}`"
        result = await self.session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]

    async def run_mutation(self, sql: str) -> int:
        """执行 INSERT/UPDATE/DELETE 写操作，返回受影响行数

        写操作前先 rollback 掉前面节点遗留的只读事务（SELECT/EXPLAIN 等），
        确保本次写从干净事务开始，commit 才能真正持久化到 dw。
        """
        await self.session.rollback()
        result = await self.session.execute(text(sql))
        await self.session.commit()
        return result.rowcount
