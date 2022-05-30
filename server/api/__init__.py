from fastapi import APIRouter
from .routes import ping
from .routes import users
from .routes import annotations
from .routes import auth
from .routes import projects
from .routes import project

# this router proxies all /api endpoints
router = APIRouter()

# route for testing / checking the service is reachable
router.include_router(ping.router, prefix='/ping')


# route to fetch, manage, submit item annotations
router.include_router(annotations.router, prefix='/annotations')

# route for all user-related endpoints (everything not related to authentication)
router.include_router(users.router, prefix='/users')

# route for authentication
router.include_router(auth.router, prefix='/login')

# route for general project things (aka non-project-specific)
router.include_router(projects.router, prefix='/projects')

# route for project related things
router.include_router(project.router, prefix='/project')
