import httpx2
import pytest

from sfr_mms_bot.parser import MmsNotice
from sfr_mms_bot.vosmms import LoginError, fetch_mms, parse_attachments, parse_text

NOTICE = MmsNotice(sender="0611111111", number="0622222222", password="p@ss.w0rd")
PNG = b"\x89PNG\r\n\x1a\nfake"

# Trimmed copy of viewMessage.jsp with fake values.
VIEW_MESSAGE = """
<script>
	var attachements= new Array(nbPJ);
		attachements[i] = new Attachement("IMG_1.png",
			"IMAGE",
			"true",
			"/download/Download.html?IDMSG=1&amp;PJRANG=2&amp;NAME=IMG_1.png&amp;FOLDER=INBOX",
			"/download/Download.html?IDMSG=1&amp;PJRANG=2&amp;NAME=IMG_1.png&amp;FOLDER=INBOX");
		i++;
		attachements[i] = new Attachement("clip.3gp", "VIDEO", "false", "/download/Download.html?IDMSG=1&amp;PJRANG=3", "x");
</script>
<div id="playerImage"><img id="imgDisplay" src="">
	<div id="imageTexte" class="texte">Coucou &eacute;t&eacute;<br/>ligne 2 €
</div>
</div>
<div id="playerTexte" style="display:none">
	<div class="contentTexte">
	</div>
</div>
<div id="errorTexte" class="texte">Coucou &eacute;t&eacute;<br/>ligne 2 €
</div>
"""


def test_parse_attachments() -> None:
    attachments = parse_attachments(VIEW_MESSAGE)
    assert [(a.name, a.kind, a.url, a.content_type) for a in attachments] == [
        ("IMG_1.png", "IMAGE", "/download/Download.html?IDMSG=1&PJRANG=2&NAME=IMG_1.png&FOLDER=INBOX", "image/png"),
        ("clip.3gp", "VIDEO", "/download/Download.html?IDMSG=1&PJRANG=3", "video/3gpp"),
    ]


def test_parse_text() -> None:
    assert parse_text(VIEW_MESSAGE) == "Coucou été\nligne 2 €"


def test_parse_text_only_mms() -> None:
    page = '<div id="imageTexte" class="texte"> </div><div class="contentTexte">\n Juste du texte\n</div>'
    assert parse_text(page) == "Juste du texte"


def test_parse_no_text() -> None:
    assert parse_text('<div id="imageTexte" class="texte"> \n</div><div class="contentTexte"></div>') == ""


def make_client(handler) -> httpx2.Client:
    return httpx2.Client(base_url="https://www.vosmms.com/", transport=httpx2.MockTransport(handler))


def test_login_refused() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/loginSubmit.jsp":
            return httpx2.Response(302, headers={"location": "https://www.vosmms.com/login.jsp?error=true"})
        return httpx2.Response(200, text="<html></html>")

    with pytest.raises(LoginError):
        fetch_mms(NOTICE, make_client(handler))


def test_fetches_text_and_parts() -> None:
    posted: dict[str, str] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        match request.url.path:
            case "/loginSubmit.jsp":
                posted.update(httpx2.QueryParams(request.content.decode()))
                return httpx2.Response(302, headers={"location": "https://www.vosmms.com/viewMessage.jsp"})
            case "/viewMessage.jsp":
                return httpx2.Response(200, content=VIEW_MESSAGE.encode("iso-8859-15"))
            case "/download/Download.html" if request.url.params["PJRANG"] == "2":
                return httpx2.Response(200, content=PNG, headers={"content-type": "application/octet-stream"})
            case "/download/Download.html":
                return httpx2.Response(200, content=b"3gp")
        return httpx2.Response(200, text="<html></html>")

    mms = fetch_mms(NOTICE, make_client(handler))
    assert posted == {"MSISDN": "0622222222", "PWD": "p@ss.w0rd"}
    assert mms.text == "Coucou été\nligne 2 €"
    assert [(p.filename, p.content_type, p.data) for p in mms.parts] == [
        ("IMG_1.png", "image/png", PNG),
        ("clip.3gp", "video/3gpp", b"3gp"),
    ]
