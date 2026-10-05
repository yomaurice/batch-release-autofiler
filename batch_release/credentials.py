"""Portal login details, kept in Windows Credential Manager (encrypted under your Windows account)."""
from dataclasses import dataclass

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "batch-release-autofiler"


@dataclass
class Login:
    username: str
    password: str


# Saved login for a SAP user, or None if it was never saved on this Windows account
def get_login(sap_user: str) -> Login | None:
    username = keyring.get_password(SERVICE, f"{sap_user}/username")
    password = keyring.get_password(SERVICE, f"{sap_user}/password")
    if not username or not password:
        return None
    return Login(username, password)


# Save (or replace) the portal username + password for a SAP user
def save_login(sap_user: str, username: str, password: str) -> None:
    keyring.set_password(SERVICE, f"{sap_user}/username", username.strip())
    keyring.set_password(SERVICE, f"{sap_user}/password", password)


# Forget the saved login for a SAP user
def delete_login(sap_user: str) -> None:
    for key in ("username", "password"):
        try:
            keyring.delete_password(SERVICE, f"{sap_user}/{key}")
        except PasswordDeleteError:
            pass
