import pytest

from flytekit import kwtypes, task, workflow
from flytekit.plugins import pandas
from flytekit.taskplugins.sqlite3.task import SQLite3Config, SQLite3Task

# https://www.sqlitetutorial.net/sqlite-sample-database/
from flytekit.types.file import FlyteFile

EXAMPLE_DB = "https://cdn.sqlitetutorial.net/wp-content/uploads/2018/03/chinook.zip"


def test_task_static():
    tk = SQLite3Task(
        "test",
        query_template="select * from tracks",
        task_config=SQLite3Config(
            uri=EXAMPLE_DB, compressed=True,
        ),
    )
    df = tk()
    assert df is not None


def test_task_dynamic():
    tk = SQLite3Task("test", query_template="select * from tracks",
                     task_config=SQLite3Config(uri=EXAMPLE_DB, compressed=True))
    df = tk()
    assert df is not None


def test_workflow():
    @task
    def my_task(df: pandas.DataFrame) -> int:
        return len(df[df.columns[0]])

    @workflow
    def wf(limit: int) -> int:
        return my_task(
            df=SQLite3Task(
                "test",
                query_template="select * from tracks limit {{.inputs.limit}}",
                inputs=kwtypes(limit=int),
                task_config=SQLite3Config(
                    uri=EXAMPLE_DB, compressed=True,
                ),
            )(limit=limit)
        )

    assert wf(limit=5) == 5