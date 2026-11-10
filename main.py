import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator


DATA_FILE = Path.home() / ".daily_note_tasks.json"
ACTIVITY_FILE = Path.home() / ".daily_note_activity.json"
HTML_FILE = Path(__file__).with_name("index.html")

app = FastAPI(title="今日の段取り")


class TaskCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    due_date: date | None = None

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("タスク名を入力してください")
        return value


class TaskUpdate(BaseModel):
    done: bool = False
    due_date: date | None = None
    is_active: bool | None = None


class ActivityCheckIn(BaseModel):
    activity_date: date


def load_activity() -> dict:
    try:
        activity = json.loads(ACTIVITY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"last_active_date": None, "streak": 0, "total_days": 0}
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=500, detail="利用記録を読み込めませんでした") from error

    if (
        not isinstance(activity, dict)
        or (activity.get("last_active_date") is not None
            and not isinstance(activity.get("last_active_date"), str))
        or not isinstance(activity.get("streak"), int)
        or isinstance(activity.get("streak"), bool)
        or activity["streak"] < 0
        or not isinstance(activity.get("total_days"), int)
        or isinstance(activity.get("total_days"), bool)
        or activity["total_days"] < 0
    ):
        raise HTTPException(status_code=500, detail="利用記録の形式が正しくありません")

    last_active_date = activity["last_active_date"]
    if last_active_date is not None:
        try:
            date.fromisoformat(last_active_date)
        except ValueError as error:
            raise HTTPException(
                status_code=500,
                detail="利用記録の日付の形式が正しくありません",
            ) from error
    return activity


def save_activity(activity: dict) -> None:
    try:
        ACTIVITY_FILE.write_text(
            json.dumps(activity, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as error:
        raise HTTPException(status_code=500, detail="利用記録を保存できませんでした") from error


def load_tasks() -> list[dict]:
    try:
        stored_tasks = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=500, detail="タスクを読み込めませんでした") from error

    if not isinstance(stored_tasks, list):
        raise HTTPException(status_code=500, detail="タスクデータの形式が正しくありません")

    tasks = []
    for task in stored_tasks:
        if (
            not isinstance(task, dict)
            or not isinstance(task.get("id"), str)
            or not isinstance(task.get("text"), str)
            or not isinstance(task.get("done"), bool)
        ):
            continue

        due_date = task.get("due_date")
        if due_date is not None:
            if not isinstance(due_date, str):
                raise HTTPException(status_code=500, detail="締切データの形式が正しくありません")
            try:
                date.fromisoformat(due_date)
            except ValueError as error:
                raise HTTPException(
                    status_code=500,
                    detail="締切データの形式が正しくありません",
                ) from error

        is_active = task.get("is_active", False)
        if not isinstance(is_active, bool):
            raise HTTPException(status_code=500, detail="取り組み中課題の形式が正しくありません")

        tasks.append({**task, "due_date": due_date, "is_active": is_active})
    return tasks


def save_tasks(tasks: list[dict]) -> None:
    try:
        DATA_FILE.write_text(
            json.dumps(tasks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as error:
        raise HTTPException(status_code=500, detail="タスクを保存できませんでした") from error


@app.get("/")
def home() -> FileResponse:
    return FileResponse(HTML_FILE)


@app.get("/api/tasks")
def get_tasks() -> list[dict]:
    return load_tasks()


@app.post("/api/activity/check-in")
def check_in(activity_input: ActivityCheckIn) -> dict:
    activity = load_activity()
    activity_date = activity_input.activity_date
    last_active_date = (
        date.fromisoformat(activity["last_active_date"])
        if activity["last_active_date"]
        else None
    )

    if last_active_date is None:
        activity["streak"] = 1
        activity["total_days"] = 1
    elif activity_date > last_active_date:
        activity["streak"] = (
            activity["streak"] + 1
            if activity_date == last_active_date.fromordinal(last_active_date.toordinal() + 1)
            else 1
        )
        activity["total_days"] += 1
    activity["last_active_date"] = max(activity_date, last_active_date or activity_date).isoformat()
    save_activity(activity)
    return activity


@app.post("/api/tasks", status_code=201)
def create_task(task_input: TaskCreate) -> dict:
    tasks = load_tasks()
    task = {
        "id": str(uuid4()),
        "text": task_input.text,
        "done": False,
        "due_date": task_input.due_date.isoformat() if task_input.due_date else None,
    }
    tasks.insert(0, task)
    save_tasks(tasks)
    return task


@app.delete("/api/tasks/completed")
def delete_completed_tasks() -> dict:
    tasks = [task for task in load_tasks() if not task["done"]]
    save_tasks(tasks)
    return {"deleted": True}


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, updates: TaskUpdate) -> dict:
    tasks = load_tasks()
    task = next((task for task in tasks if task["id"] == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="タスクが見つかりません")

    if "done" in updates.model_fields_set:
        task["done"] = updates.done
        if updates.done:
            task["is_active"] = False
    if "due_date" in updates.model_fields_set:
        task["due_date"] = updates.due_date.isoformat() if updates.due_date else None
    if "is_active" in updates.model_fields_set:
        if updates.is_active and task["done"]:
            raise HTTPException(status_code=409, detail="完了した課題は開始できません")
        if updates.is_active:
            for existing_task in tasks:
                existing_task["is_active"] = existing_task["id"] == task_id
        else:
            task["is_active"] = False
    save_tasks(tasks)
    return task


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> dict:
    tasks = load_tasks()
    remaining_tasks = [task for task in tasks if task["id"] != task_id]
    if len(remaining_tasks) == len(tasks):
        raise HTTPException(status_code=404, detail="タスクが見つかりません")
    save_tasks(remaining_tasks)
    return {"deleted": True}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
