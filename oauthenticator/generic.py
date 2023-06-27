"""
Custom Authenticator to use generic OAuth2 with JupyterHub
"""
import os
from functools import reduce

from jupyterhub.auth import LocalAuthenticator
from jupyterhub.traitlets import Callable
from tornado.httpclient import AsyncHTTPClient
from traitlets import Bool, Dict, Set, Unicode, Union, default

from .oauth2 import OAuthenticator


class GenericOAuthenticator(OAuthenticator):
    _deprecated_oauth_aliases = {
        "username_key": ("username_claim", "16.0.0"),
        "extra_params": ("token_params", "16.0.0"),
        **OAuthenticator._deprecated_oauth_aliases,
    }

    extra_params = Dict(
        help="Deprecated, use `GenericOAuthenticator.token_params`"
    ).tag(config=True)

    login_service = Unicode("OAuth 2.0", config=True)

    claim_groups_key = Union(
        [Unicode(os.environ.get('OAUTH2_GROUPS_KEY', 'groups')), Callable()],
        config=True,
        help="""
        Userdata groups claim key from returned json for USERDATA_URL.

        Can be a string key name (use periods for nested keys), or a callable
        that accepts the returned json (as a dict) and returns the groups list.
        """,
    )

    allowed_groups = Set(
        Unicode(),
        config=True,
        help="Automatically allow members of selected groups",
    )

    admin_groups = Set(
        Unicode(),
        config=True,
        help="Groups whose members should have Jupyterhub admin privileges",
    )

    username_key = Union(
        [Unicode(os.environ.get('OAUTH2_USERNAME_KEY', 'username')), Callable()],
        config=True,
        help="""Deprecated, use `GenericOAuthenticator.username_claim`""",
    )

    username_claim = Union(
        [Unicode(os.environ.get('OAUTH2_USERNAME_KEY', 'username')), Callable()],
        config=True,
        help="""
        Userdata username key from returned json for USERDATA_URL.

        Can be a string key name or a callable that accepts the returned
        json (as a dict) and returns the username.  The callable is useful
        e.g. for extracting the username from a nested object in the
        response.
        """,
    )

    tls_verify = Bool(
        os.environ.get('OAUTH2_TLS_VERIFY', 'True').lower() in {'true', '1'},
        config=True,
        help="Require valid tls certificates in HTTP requests",
    )

    @default("basic_auth")
    def _basic_auth_default(self):
        return os.environ.get('OAUTH2_BASIC_AUTH', 'True').lower() in {'true', '1'}

    @default("http_client")
    def _default_http_client(self):
        return AsyncHTTPClient(
            force_instance=True, defaults=dict(validate_cert=self.tls_verify)
        )

    def user_info_to_username(self, user_info):
        """
        Overrides OAuthenticator.user_info_to_username to support the
        GenericOAuthenticator unique feature of allowing username_claim to be a
        callable function.
        """
        if callable(self.username_claim):
            username = self.username_claim(user_info)
        else:
            username = user_info.get(self.username_claim, None)
            if not username:
                message = (f"No {self.username_claim} found in {user_info}",)
                self.log.error(message)
                raise ValueError(message)

        return username

    def get_user_groups(self, user_info):
        """
        Returns a set of groups the user belongs to based on claim_groups_key
        and provided user_info.

        - If claim_groups_key is a callable, it is meant to return the groups
          directly.
        - If claim_groups_key is a nested dictionary key like
          "permissions.groups", this function returns
          user_info["permissions"]["groups"].

        Note that this method is introduced by GenericOAuthenticator and not
        present in the base class.
        """
        if callable(self.claim_groups_key):
            return set(self.claim_groups_key(user_info))
        try:
            return set(reduce(dict.get, self.claim_groups_key.split("."), user_info))
        except TypeError:
            self.log.error(
                f"The claim_groups_key {self.claim_groups_key} does not exist in the user token"
            )
            return set()

    async def update_auth_model(self, auth_model):
        """
        Sets admin status to True or False if `admin_groups` is configured and
        the user isn't part of `admin_users` or `admin_groups`. Note that
        leaving it at None makes users able to retain an admin status while
        setting it to False makes it be revoked.
        """
        if auth_model["admin"]:
            # auth_model["admin"] being True means the user was in admin_users
            return auth_model

        if self.admin_groups:
            # admin status should in this case be True or False, not None
            user_info = auth_model["auth_state"][self.user_auth_state_key]
            user_groups = self.get_user_groups(user_info)
            auth_model["admin"] = any(user_groups & self.admin_groups)

        return auth_model

    async def check_allowed(self, username, auth_model):
        """
        Overrides the OAuthenticator.check_allowed to also allow users part of
        `allowed_groups`.
        """
        if await super().check_allowed(username, auth_model):
            return True

        if self.allowed_groups:
            user_info = auth_model["auth_state"][self.user_auth_state_key]
            user_groups = self.get_user_groups(user_info)
            if any(user_groups & self.allowed_groups):
                return True

        # users should be explicitly allowed via config, otherwise they aren't
        return False


class LocalGenericOAuthenticator(LocalAuthenticator, GenericOAuthenticator):
    """A version that mixes in local system user creation"""
