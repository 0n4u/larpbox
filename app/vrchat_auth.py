from __future__ import annotations
import time
from dataclasses import dataclass
from http.cookiejar import Cookie
from typing import Any
import vrchatapi
from vrchatapi.api import authentication_api
from vrchatapi.exceptions import ApiException, UnauthorizedException
from vrchatapi.models.two_factor_auth_code import TwoFactorAuthCode
from vrchatapi.models.two_factor_email_code import TwoFactorEmailCode
from .api_rate_limit import rate_limited_vrchat_call
from .config import clear_auth_session, get_bool, load_config, save_auth_session
from .logging_setup import get_logger
from .services.auth_errors import AuthSessionError, api_error_text, auth_session_error_from_api
logger = get_logger('auth')
USER_AGENT = 'VRCX/2024.1.0'
TWO_FACTOR_TOTP = '2 Factor Authentication'
TWO_FACTOR_EMAIL = 'Email 2 Factor Authentication'

@dataclass(frozen=True)
class VRChatSession:
    user_id: str
    display_name: str
    username: str
    auth_token: str
    two_factor_token: str | None = None

class TwoFactorRequired(Exception):

    def __init__(self, method: str):
        super().__init__(method)
        self.method = method

class LoginFailed(Exception):
    pass

def _extract_cookies(api_client: vrchatapi.ApiClient) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for cookie in api_client.rest_client.cookie_jar:
        cookies[cookie.name] = cookie.value
    return cookies

def _parse_set_cookie_headers(headers: Any) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if headers is None:
        return cookies
    values: list[str] = []
    if hasattr(headers, 'getlist'):
        try:
            values.extend(headers.getlist('Set-Cookie') or [])
        except Exception:
            pass
    if not values and hasattr(headers, 'get'):
        raw = headers.get('Set-Cookie') or headers.get('set-cookie')
        if raw:
            values.append(raw)
    if not values and isinstance(headers, dict):
        raw = headers.get('Set-Cookie') or headers.get('set-cookie')
        if raw:
            values.append(raw)
    for value in values:
        if not value:
            continue
        pair = value.split(';', 1)[0]
        if '=' not in pair:
            continue
        name, token = pair.split('=', 1)
        cookies[name.strip()] = token.strip()
    return cookies

def _extract_session_tokens(api_client: vrchatapi.ApiClient, headers: Any=None) -> tuple[str, str | None]:
    jar_cookies = _extract_cookies(api_client)
    header_cookies = _parse_set_cookie_headers(headers)
    config_auth = str(api_client.configuration.api_key.get('authCookie') or '').strip()
    config_2fa = str(api_client.configuration.api_key.get('twoFactorAuthCookie') or '').strip()
    auth_token = (jar_cookies.get('auth') or header_cookies.get('auth') or config_auth or '').strip()
    two_factor_token = jar_cookies.get('twoFactorAuth') or header_cookies.get('twoFactorAuth') or config_2fa or None
    if two_factor_token is not None:
        two_factor_token = two_factor_token.strip() or None
    return (auth_token, two_factor_token)

def _make_vrchat_cookie(name: str, value: str) -> Cookie:
    return Cookie(0, name, value, None, False, 'api.vrchat.cloud', True, False, '/', False, False, int(time.time()) + 86400 * 30, False, None, None, {})

def _configure_api_client(api_client: vrchatapi.ApiClient, *, auth_token: str | None=None, two_factor_token: str | None=None) -> None:
    api_client.user_agent = USER_AGENT
    api_client.default_headers['Referer'] = 'https://vrcx.app'
    api_client.default_headers['Accept'] = 'application/json'
    api_client.configuration.api_key.pop('authCookie', None)
    api_client.configuration.api_key.pop('twoFactorAuthCookie', None)
    api_client.cookie = None
    if auth_token:
        jar = api_client.rest_client.cookie_jar
        jar.set_cookie(_make_vrchat_cookie('auth', auth_token))
        if two_factor_token:
            jar.set_cookie(_make_vrchat_cookie('twoFactorAuth', two_factor_token))
        cookie_parts = [f'auth={auth_token}']
        if two_factor_token:
            cookie_parts.append(f'twoFactorAuth={two_factor_token}')
        api_client.cookie = '; '.join(cookie_parts)

def _apply_rate_limit(api_client: vrchatapi.ApiClient) -> None:
    original_call_api = api_client.call_api

    def rate_limited_call_api(*args: Any, **kwargs: Any) -> Any:
        return rate_limited_vrchat_call(original_call_api, *args, **kwargs)
    api_client.call_api = rate_limited_call_api

def _rate_limit_message(exc: Exception) -> str | None:
    detail = api_error_text(exc).casefold()
    status = getattr(exc, 'status', None)
    if status == 429 or 'too many' in detail or 'rate limit' in detail:
        return 'VRChat rate limit reached — wait a few minutes before trying again.'
    return None

def _make_configuration(username: str | None=None, password: str | None=None) -> vrchatapi.Configuration:
    configuration = vrchatapi.Configuration()
    if username and password:
        configuration.username = username
        configuration.password = password
    return configuration

def _session_from_user(user: Any, api_client: vrchatapi.ApiClient, username: str, headers: Any=None) -> VRChatSession:
    auth_token, two_factor_token = _extract_session_tokens(api_client, headers)
    if not auth_token:
        raise LoginFailed('Login succeeded but no auth token was returned.')
    return VRChatSession(user_id=str(getattr(user, 'id', '') or ''), display_name=str(getattr(user, 'display_name', '') or username), username=username, auth_token=auth_token, two_factor_token=two_factor_token)

def _fetch_authenticated_user(auth_api: authentication_api.AuthenticationApi, api_client: vrchatapi.ApiClient, username: str) -> VRChatSession:
    current_user, _status, headers = auth_api.get_current_user_with_http_info()
    return _session_from_user(current_user, api_client, username, headers)

def verify_session(session: VRChatSession) -> VRChatSession:
    configuration = _make_configuration()
    try:
        with vrchatapi.ApiClient(configuration) as api_client:
            _configure_api_client(api_client, auth_token=session.auth_token, two_factor_token=session.two_factor_token)
            _apply_rate_limit(api_client)
            auth_api = authentication_api.AuthenticationApi(api_client)
            current_user, _, headers = auth_api.get_current_user_with_http_info()
            refreshed_auth, refreshed_tfa = _extract_session_tokens(api_client, headers)
            return VRChatSession(user_id=str(getattr(current_user, 'id', '') or session.user_id), display_name=str(getattr(current_user, 'display_name', '') or session.display_name), username=session.username, auth_token=refreshed_auth or session.auth_token, two_factor_token=refreshed_tfa or session.two_factor_token)
    except AuthSessionError:
        raise
    except (UnauthorizedException, ApiException) as exc:
        rate_msg = _rate_limit_message(exc)
        if rate_msg:
            raise AuthSessionError(rate_msg) from exc
        auth_error = auth_session_error_from_api(exc)
        if auth_error is not None:
            raise auth_error
        detail = api_error_text(exc)
        raise AuthSessionError(detail or str(exc)) from exc

def _login_with_client(auth_api: authentication_api.AuthenticationApi, api_client: vrchatapi.ApiClient, username: str, two_factor_code: str | None=None, two_factor_method: str | None=None) -> VRChatSession:
    try:
        return _fetch_authenticated_user(auth_api, api_client, username)
    except UnauthorizedException as exc:
        if exc.status != 200:
            raise LoginFailed(str(exc.reason or exc)) from exc
        if not two_factor_method:
            if TWO_FACTOR_EMAIL in (exc.reason or ''):
                raise TwoFactorRequired('email') from exc
            if TWO_FACTOR_TOTP in (exc.reason or ''):
                raise TwoFactorRequired('totp') from exc
            raise LoginFailed(exc.reason or 'Authentication failed.') from exc
        if not two_factor_code:
            raise TwoFactorRequired(two_factor_method) from exc
        if two_factor_method == 'email':
            auth_api.verify2_fa_email_code(two_factor_email_code=TwoFactorEmailCode(two_factor_code.strip()))
        else:
            auth_api.verify2_fa(two_factor_auth_code=TwoFactorAuthCode(two_factor_code.strip()))
        return _fetch_authenticated_user(auth_api, api_client, username)
    except ApiException as exc:
        rate_msg = _rate_limit_message(exc)
        if rate_msg:
            raise LoginFailed(rate_msg) from exc
        raise LoginFailed(str(exc.reason or exc)) from exc

def login(username: str, password: str, two_factor_code: str | None=None, two_factor_method: str | None=None) -> VRChatSession:
    username = username.strip()
    password = password.strip()
    if not username or not password:
        raise LoginFailed('Username and password are required.')
    configuration = _make_configuration(username=username, password=password)
    with vrchatapi.ApiClient(configuration) as api_client:
        _configure_api_client(api_client)
        _apply_rate_limit(api_client)
        auth_api = authentication_api.AuthenticationApi(api_client)
        session = _login_with_client(auth_api, api_client, username, two_factor_code=two_factor_code, two_factor_method=two_factor_method)
    logger.info('Logged in as %s (%s)', session.display_name, session.user_id)
    return session

def restore_session() -> VRChatSession | None:
    config = load_config()
    if not get_bool(config.get('remember_login')):
        return None
    auth_token = str(config.get('auth_token') or '').strip()
    if not auth_token:
        logger.debug('Remember me enabled but no auth token stored')
        return None
    username = str(config.get('auth_username') or 'VRChat User')
    two_factor_token = str(config.get('two_factor_token') or '').strip() or None
    seed = VRChatSession(user_id=str(config.get('auth_user_id') or ''), display_name=str(config.get('auth_display_name') or username), username=username, auth_token=auth_token, two_factor_token=two_factor_token)
    try:
        session = verify_session(seed)
    except AuthSessionError as exc:
        logger.warning('Saved VRChat session invalid (%s) — login required', exc)
        clear_auth_session()
        return None
    except Exception as exc:
        logger.warning('Could not restore VRChat session — login required', exc_info=True)
        clear_auth_session()
        return None
    save_auth_session(auth_token=session.auth_token, two_factor_token=session.two_factor_token, username=session.username, display_name=session.display_name, user_id=session.user_id, remember_login=True)
    logger.info('Restored session for %s', session.display_name)
    return session

def persist_session(session: VRChatSession, remember: bool) -> None:
    if not remember:
        clear_auth_session()
        return
    if not session.auth_token:
        logger.warning('Remember me checked but auth token missing — session not saved')
        clear_auth_session()
        return
    save_auth_session(auth_token=session.auth_token, two_factor_token=session.two_factor_token, username=session.username, display_name=session.display_name, user_id=session.user_id, remember_login=True)
    logger.info('Saved VRChat session for remember me (%s)', session.display_name)
