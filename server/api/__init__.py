from fastapi import APIRouter
from .routes import ping
from .routes import admin
from .routes import annotations
from .routes import login

# this router proxies all /api endpoints
router = APIRouter()

# route for testing / checking the service is reachable
router.include_router(ping.router, prefix='/ping')

# route for all admin-related endpoints
router.include_router(admin.router, prefix='/admin')

# route to fetch, manage, submit item annotations
router.include_router(annotations.router, prefix='/annotations')

# route for authentication
router.include_router(login.router, prefix='/login')
