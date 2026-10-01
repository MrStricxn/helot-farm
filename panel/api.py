# panel/api.py
"""REST-панель фермы (FastAPI): тонкий слой над FarmManager."""
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

try:
    from panel.manager import FarmManager
    from panel.models import Base, TaskType
except ImportError:  # запуск как `python panel/api.py`
    from manager import FarmManager
    from models import Base, TaskType


DATABASE_URL = os.getenv("PANEL_DB", "sqlite:///./panel.db")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_manager():
    """На каждый запрос: новая сессия + инстанс FarmManager."""
    session = SessionLocal()
    try:
        yield FarmManager(session)
    finally:
        session.close()


# --- Request-схемы ---


class AccountCreate(BaseModel):
    phone_number: str


class TaskCreate(BaseModel):
    account_id: int
    task_type: str
    payload: dict = {}


class TaskUpdate(BaseModel):
    status: str
    result: dict | None = None


# --- Сериализаторы ---


def _account_to_dict(a):
    return {
        "id": a.id,
        "phone_number": a.phone_number,
        "status": a.status.value,
        "registration_data": a.registration_data,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


def _device_to_dict(d):
    return {
        "id": d.id,
        "serial": d.serial,
        "status": d.status.value,
        "account_id": d.account_id,
    }


def _task_to_dict(t):
    return {
        "id": t.id,
        "account_id": t.account_id,
        "task_type": t.task_type.value,
        "payload": t.payload,
        "status": t.status.value,
        "result": t.result,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Helot Panel", lifespan=lifespan)


# --- Accounts ---


@app.post("/accounts", status_code=201)
def create_account(body: AccountCreate, manager: FarmManager = Depends(get_manager)):
    try:
        return _account_to_dict(manager.create_account(body.phone_number))
    except Exception as e:
        if "UNIQUE" in str(e).upper():
            raise HTTPException(status_code=409, detail="phone_number already exists")
        raise


@app.get("/accounts")
def get_accounts(manager: FarmManager = Depends(get_manager)):
    return [_account_to_dict(a) for a in manager.list_accounts()]


@app.post("/accounts/{account_id}/assign_device")
def assign_device(account_id: int, manager: FarmManager = Depends(get_manager)):
    try:
        device = manager.assign_free_device_to_account(account_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if device is None:
        raise HTTPException(status_code=409, detail="No free devices available")
    return _device_to_dict(device)


# --- Devices ---


@app.get("/devices")
def get_devices(manager: FarmManager = Depends(get_manager)):
    return [_device_to_dict(d) for d in manager.list_devices()]


@app.post("/devices/{device_id}/release")
def release_device(device_id: int, manager: FarmManager = Depends(get_manager)):
    device = manager.release_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"status": "success", "device": _device_to_dict(device)}


@app.get("/devices/{serial}/next_task", status_code=200)
def next_task(serial: str, manager: FarmManager = Depends(get_manager)):
    task = manager.get_next_task_for_device(serial)
    if task is None:
        return {"status": "empty", "task": None}
    return {"status": "success", "task": _task_to_dict(task)}


# --- Tasks ---


@app.post("/tasks", status_code=201)
def create_task(body: TaskCreate, manager: FarmManager = Depends(get_manager)):
    try:
        TaskType(body.task_type)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Unknown task_type: {body.task_type}")
    try:
        return _task_to_dict(manager.create_task(body.account_id, body.task_type, body.payload))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/tasks")
def get_tasks(manager: FarmManager = Depends(get_manager)):
    return [_task_to_dict(t) for t in manager.list_tasks()]


@app.patch("/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate, manager: FarmManager = Depends(get_manager)):
    try:
        task = manager.update_task_status(task_id, body.status, result=body.result)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Unknown status: {body.status}")
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_dict(task)


# Статика дашборда — ПОСЛЕ всех API-маршрутов: API матчится первым,
# остальное (включая "/") отдает panel/static (index.html).
app.mount(
    "/",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True),
    name="static",
)


def run_api(host="0.0.0.0", port=8000):
    """Запуск API-сервера панели."""
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", host),
        port=int(os.getenv("PORT", port)),
    )


if __name__ == "__main__":
    run_api()
