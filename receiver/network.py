"""HTTP transport that keeps secrets on the explicitly selected destination."""

from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class RejectRedirects(HTTPRedirectHandler):
    """Let urllib raise HTTPError instead of forwarding headers or bodies."""

    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


# Ignore ambient HTTP(S)_PROXY settings. TLS certificate verification uses the
# platform trust store, as with urllib's default HTTPS handler.
_OPENER = build_opener(ProxyHandler({}), RejectRedirects())


def urlopen(request, timeout=25):
    return _OPENER.open(request, timeout=timeout)
