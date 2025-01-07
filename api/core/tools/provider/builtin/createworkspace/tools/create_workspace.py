import hashlib
import uuid
from datetime import datetime
from typing import Any, Union

import psycopg2
import redis
from Crypto.PublicKey import RSA
from flask import g
from psycopg2 import sql

from core.tools.entities.tool_entities import ToolInvokeMessage
from core.tools.tool.builtin_tool import BuiltinTool


class CurrentTimeTool(BuiltinTool):

    def _invoke(self,
                user_id: str,
                tool_parameters: dict[str, Any],
                ) -> Union[ToolInvokeMessage, list[ToolInvokeMessage]]:
        # 数据库相关配置信息
        # 配置数据库连接参数
        host = '0.0.0.0'  # 数据库主机
        port = '5432'
        dbname = 'dify'  # 数据库名
        user = 'postgres'  # 用户名
        password = 'difyai123456'  # 密码
        current_tenant_id = g.get('tenant_id')

        # 连接 Redis 时提供密码
        redis_host = '0.0.0.0'  # Redis 服务器地址
        redis_port = 6379  # Redis 服务器端口，默认为 6379
        redis_password = 'difyai123456'  # 替换为你的 Redis 密码

        """
            invoke tools
        """
        # get timezone
        workspace_name = tool_parameters.get('workspace_name')
        operate_type = tool_parameters.get('operate_type')
        whether_force_delete = tool_parameters.get('force_delete')
        workspace_name_list = workspace_name.split('|')
        # 判断输入的工作空间中是否有重复元素
        workspace_name_set = set(workspace_name_list)
        if len(workspace_name_set) != len(workspace_name_list):
            return self.create_text_message('操作工作空间失败，失败原因为：输入的工作空间中，存在重复名称')

        connection = psycopg2.connect(
            host=host,
            dbname=dbname,
            user=user,
            password=password,
            port=port
        )
        cursor = connection.cursor()  # 创建一个游标对象，用于执行SQL查询
        if operate_type == 'create':
            # 根据输入空间名称进行查询，判断数据库中是否存在同名称的工作空间
            query = "SELECT name FROM tenants "

            # 执行查询
            cursor.execute(query)
            # 获取查询结果
            results = cursor.fetchall()
            # 数据库中的工作空间名称
            results = [item[0] for item in results]
            exist_workspace_list = list(filter(lambda item: item in workspace_name_list, results))
            # 存在重复名称
            if exist_workspace_list:
                return self.create_text_message(
                    '创建工作空间失败,失败原因为:数据库中已存在名称为%s的命名空间' % exist_workspace_list)

            for work_space_name in workspace_name_list:
                # 连接到 PostgreSQL 数据库
                try:
                    # 生成RSA 公钥和私钥
                    private_key = RSA.generate(2048)
                    public_key = private_key.publickey()
                    pem_public = public_key.export_key().decode()
                    pem_private = private_key.export_key().decode()

                    # 生成工作空间id
                    workspace_id = uuid.uuid1().__str__()
                    # 新建工作空间，执行sql
                    insert_query = """
                                   INSERT INTO public.tenants (
                                       id, name, encrypt_public_key, plan, status, created_at, updated_at, custom_config
                                   ) VALUES (
                                       %s, %s, %s, %s, %s, %s, %s, %s
                                   )
                               """
                    current_time = datetime.now()
                    # 执行插入语句
                    cursor.execute(insert_query, (
                        workspace_id,  # 自动生成的 UUID
                        work_space_name,  # 租户名称
                        pem_public,  # 可选的公钥
                        'basic',  # 计划类型，默认为 'basic'
                        'normal',  # 状态，默认为 'normal'
                        current_time,  # 创建时间
                        current_time,  # 更新时间
                        ''  # 可选的自定义配置
                    ))

                    # 提交事务

                    print('创建工作空间成功，空间id为%s' % workspace_id)

                    # 开始创建关联关系，向关联表中插入用户&&空间关系
                    insert_query = sql.SQL("""
                            INSERT INTO public.tenant_account_joins (
                                tenant_id, account_id, role, invited_by, created_at, updated_at, current
                            ) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            RETURNING id;  -- 返回插入的 id，便于后续处理
                        """)
                    cursor.execute(insert_query,
                                   (workspace_id, user_id, 'owner', None, current_time, current_time, 't'))
                    inserted_id = cursor.fetchone()[0]
                    print('插入关联表成功，插入的关联表id为%s' % inserted_id)
                    connection.commit()

                    # 开始向redis中强行插入私钥信息,加密获取redis key
                    filepath = "privkeys/{tenant_id}".format(tenant_id=workspace_id) + "/private.pem"
                    cache_key = 'tenant_privkey:{hash}'.format(hash=hashlib.sha3_256(filepath.encode()).hexdigest())
                    # 创建 Redis 连接实例
                    try:
                        client = redis.StrictRedis(
                            host=redis_host,
                            port=redis_port,
                            password=redis_password,  # 提供密码
                            db=0,  # 默认数据库索引为 0
                            decode_responses=True  # 如果返回的值是字符串，自动解码为字符串
                        )

                        # 测试连接
                        if client.ping():
                            print("连接成功！")
                            client.set(cache_key, pem_private)
                            print('向redis中插入私钥成功')
                        else:
                            print("连接失败！")
                    except redis.ConnectionError as e:
                        print(f"连接 Redis 时出错: {e}")
                    print('********创建工作空间:%s全流程成功**************' % work_space_name)
                except Exception as error:
                    connection.rollback()
                    print(f"插入数据时出错：{error}")
            # 关闭游标和连接
            if cursor:
                cursor.close()
            if connection:
                connection.close()
            return self.create_text_message('创建工作空间:%s全流程成功' % workspace_name_list)

        if operate_type == 'delete':
            try:
                # 清除对应的工作空间表、关联关系表、清除redis缓存
                # 构造 SQL 查询，删除 my_column 等于列表中任意值的行
                select_tenants_id_by_name_query = delete_tenants_query = """
                      select id FROM tenants
                      WHERE name in %s;
                """
                # 执行查询
                cursor.execute(select_tenants_id_by_name_query, (tuple(workspace_name_list),))
                results = cursor.fetchall()
                if results:
                    # 数据库中的工作空间id
                    delete_tenants_id_list = [str(item[0]) for item in results]
                    if current_tenant_id in delete_tenants_id_list:
                        return self.create_text_message(
                            '删除工作空间失败，失败原因为:删除的工作空间包含当前工作空间,禁止在工作空间内删除自身空间')

                    # 开始进行权限校验，关联关系表中,
                    role_tenants_join_query = """
                             SELECT
	                    tenant_account_joins.account_id,
	                    accounts."name" as user_name,
	                    tenants."name" as workspace_name
                            FROM
	                    tenant_account_joins
	                    LEFT JOIN accounts ON tenant_account_joins.account_id = accounts.ID 
	                    left join tenants on tenant_account_joins.tenant_id = tenants.id
	                    WHERE 
	                    tenant_account_joins."role" = 'owner' and  tenant_id in %s ;
                    """
                    cursor.execute(role_tenants_join_query, (tuple(delete_tenants_id_list),))
                    info_with_role_list = cursor.fetchall()
                    if info_with_role_list:
                        for info_with_role in info_with_role_list:
                            tmp_account_id = str(info_with_role[0])
                            tmp_username = str(info_with_role[1])
                            tmp_workspace_name = str(info_with_role[2])
                            if tmp_account_id != user_id:
                                # 删除空间的用户非 空间所属用户
                                return self.create_text_message(
                                    '删除工作空间失败，失败原因为:工作空间%s所属用户为%s,非当前操作用户，无法进行删除操作' % (
                                        tmp_workspace_name, tmp_username))
                    else:
                        return self.create_text_message('删除工作空间失败，失败原因为:工作空间无所属用户')

                    if whether_force_delete == 'notForce':
                        role_tenants_join_query = """
                                                     select tenants."name"
                                                    from apps 
                                                    left join tenants
                                                    on apps.tenant_id = tenants."id"
                        	                        WHERE 
                        	                        apps.tenant_id in %s ;
                                            """
                        cursor.execute(role_tenants_join_query, (tuple(delete_tenants_id_list),))
                        exsit_app_result = cursor.fetchall()
                        if exsit_app_result:
                            exsit_app_workspace_name_list = list(set([str(item[0]) for item in exsit_app_result]))
                            return self.create_text_message(
                                '删除工作空间失败，失败原因为:工作空间%s内，仍然存在app应用，清前往清空或者选择【强制删除】' % exsit_app_workspace_name_list)

                    delete_tenants_query = """
                          DELETE FROM tenants
                          WHERE name in %s;
                    """
                    # 执行查询
                    cursor.execute(delete_tenants_query, (tuple(workspace_name_list),))
                    print('删除工作空间表数据成功')

                    delete_tenants_join_query = """
                          DELETE FROM tenant_account_joins
                          WHERE tenant_id in %s;
                    """
                    cursor.execute(delete_tenants_join_query, (tuple(delete_tenants_id_list),))
                    print('删除工作关联关系表数据成功')

                    # 开始进行剔除redis中的缓存数据
                    client = redis.StrictRedis(
                        host=redis_host,
                        port=redis_port,
                        password=redis_password,  # 提供密码
                        db=0,  # 默认数据库索引为 0
                        decode_responses=True  # 如果返回的值是字符串，自动解码为字符串
                    )

                    redis_delete_list = []
                    for delete_tenants_id in delete_tenants_id_list:
                        # 开始向redis中强行插入私钥信息,加密获取redis key
                        filepath = "privkeys/{tenant_id}".format(tenant_id=delete_tenants_id) + "/private.pem"
                        cache_key = 'tenant_privkey:{hash}'.format(hash=hashlib.sha3_256(filepath.encode()).hexdigest())
                        redis_delete_list.append(cache_key)
                    # 开始进行删除redis私钥信息
                    connection.commit()
                    try:
                        # 测试连接
                        if client.ping():
                            print("连接成功！")
                            client.delete(*redis_delete_list)
                            return self.create_text_message('删除工作空间:%s全流程成功' % workspace_name_list)
                        else:
                            print("连接失败！")
                    except redis.ConnectionError as e:
                        raise Exception('redis操作失败')
                else:
                    return self.create_text_message(
                        '删除工作空间:%s失败，失败原因:库中不存在对应名称的工作空间' % workspace_name_list)
            except Exception as e:
                connection.rollback()
                return self.create_text_message('删除工作空间:%s失败，失败原因:' % e.__str__())
