import logging
from collections.abc import Mapping
from enum import StrEnum
from threading import Lock
from typing import Any, Optional

from pydantic import BaseModel

from core.helper.code_executor.javascript.javascript_transformer import NodeJsTemplateTransformer
from core.helper.code_executor.jinja2.jinja2_transformer import Jinja2TemplateTransformer
from core.helper.code_executor.python3.python3_transformer import Python3TemplateTransformer
from core.helper.code_executor.template_transformer import TemplateTransformer

logger = logging.getLogger(__name__)


class CodeExecutionError(Exception):
    pass


class CodeExecutionResponse(BaseModel):
    class Data(BaseModel):
        stdout: Optional[str] = None
        error: Optional[str] = None

    code: int
    message: str
    data: Data


class CodeLanguage(StrEnum):
    PYTHON3 = "python3"
    JINJA2 = "jinja2"
    JAVASCRIPT = "javascript"


class CodeExecutor:
    dependencies_cache: dict[str, str] = {}
    dependencies_cache_lock = Lock()

    code_template_transformers: dict[CodeLanguage, type[TemplateTransformer]] = {
        CodeLanguage.PYTHON3: Python3TemplateTransformer,
        CodeLanguage.JINJA2: Jinja2TemplateTransformer,
        CodeLanguage.JAVASCRIPT: NodeJsTemplateTransformer,
    }

    code_language_to_running_language = {
        CodeLanguage.JAVASCRIPT: "nodejs",
        CodeLanguage.JINJA2: CodeLanguage.PYTHON3,
        CodeLanguage.PYTHON3: CodeLanguage.PYTHON3,
    }

    supported_dependencies_languages: set[CodeLanguage] = {CodeLanguage.PYTHON3}

    @classmethod
    def execute_code(cls, language: CodeLanguage, preload: str, code: str) -> str:
        """
        Execute code
        :param language: code language
        :param code: code
        :return:
        """
        response = {}
        response['code'] = 0
        response['message'] = 'success'
        response['data'] = {}
        try:
            # response = response.json()
            # 动态编译并执行代码
            # 创建一个字典来存放全局/局部变量
            context = {}

            # 执行代码并将所有变量放入context中
            exec(code, context)

            # 从context中获取执行的结果
            final_result = context.get('result')
            response['data']['error'] = ''
            response['data']['stdout'] = final_result
        except Exception as e:
            response['data']['error'] = str(e)
            response['data']['stdout'] = ''

        if (code := response.get("code")) != 0:
            raise CodeExecutionError(f"Got error code: {code}. Got error msg: {response.get('message')}")

        response_code = CodeExecutionResponse(**response)

        if response_code.data.error:
            raise CodeExecutionError(response_code.data.error)

        return response_code.data.stdout or ""

    @classmethod
    def execute_workflow_code_template(cls, language: CodeLanguage, code: str, inputs: Mapping[str, Any]):
        """
        Execute code
        :param language: code language
        :param code: code
        :param inputs: inputs
        :return:
        """
        template_transformer = cls.code_template_transformers.get(language)
        if not template_transformer:
            raise CodeExecutionError(f"Unsupported language {language}")

        runner, preload = template_transformer.transform_caller(code, inputs)

        try:
            response = cls.execute_code(language, preload, runner)
        except CodeExecutionError as e:
            raise e

        return template_transformer.transform_response(response)
