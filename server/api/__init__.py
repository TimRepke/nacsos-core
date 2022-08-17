from fastapi import APIRouter
from .routes import ping
from .routes import users
from .routes import annotations
from .routes import auth
from .routes import projects
from .routes import project
from .routes import imports
from .routes import events

# this router proxies all /api endpoints
router = APIRouter()

# route for testing / checking the service is reachable
router.include_router(ping.router, prefix='/ping')

# route to fetch, manage, submit item annotations
router.include_router(annotations.router, prefix='/annotations', tags=['annotations'])

# route for all user-related endpoints (everything not related to authentication)
router.include_router(users.router, prefix='/users', tags=['users'])

# route for authentication
router.include_router(auth.router, prefix='/login', tags=['oauth'])

# route for general project things (aka non-project-specific)
router.include_router(projects.router, prefix='/projects', tags=['projects'])

# route for project related things
router.include_router(project.router, prefix='/project', tags=['project'])

# route for project related things
router.include_router(imports.router, prefix='/imports', tags=['imports'])

# route for triggering events in the system
router.include_router(events.router, prefix='/events', tags=['events'])
