from typing import Optional
from nacsos_data.schemas.users import UserModel, UserInDBModel
from server.util.config import settings

fake_user_db = {
    'user1': UserInDBModel(id='b0949d0ee3e147c39a5da2cbbdc2ea23', username='user1',
                           full_name='User One', email='user1@addr.net',
                           password='$2b$12$wxfjv2pPPX69nP0g2n0omO8txGVcUKkeKIFN6mChN6dtXKT.wXDXG',  # 1234
                           is_active=True, is_superuser=True),
    'user2': UserInDBModel(id='a385234054fd410b8de63db6caa0855a', username='user2',
                           full_name='User Two', email='user2@addr.net',
                           password='$2b$12$wxfjv2pPPX69nP0g2n0omO8txGVcUKkeKIFN6mChN6dtXKT.wXDXG',  # 1234
                           is_active=False, is_superuser=True),
    'user3': UserInDBModel(id='acbd3dab9f3841dea871e001c15e97f3', username='user3',
                           full_name='User Three', email='user3@addr.net',
                           password='$2b$12$wxfjv2pPPX69nP0g2n0omO8txGVcUKkeKIFN6mChN6dtXKT.wXDXG',  # 1234
                           is_active=False, is_superuser=False),
    'user4': UserInDBModel(id='b918d7614e0a4fb2886c8f6a741ae106', username='user4',
                           full_name='User Four', email='user4@addr.net',
                           password='$2b$12$wxfjv2pPPX69nP0g2n0omO8txGVcUKkeKIFN6mChN6dtXKT.wXDXG',  # 1234
                           is_active=True, is_superuser=False),

}


def get_user(uid: Optional[int] = None, username: Optional[str] = None) -> Optional[UserModel]:
    # TODO db: Session = Depends(get_db)

    if username is not None and username in fake_user_db:
        return fake_user_db[username]
    if uid is not None:
        return None  # TODO: return user by id
    return None


def get_users() -> list[UserModel]:
    return list(fake_user_db.values())
