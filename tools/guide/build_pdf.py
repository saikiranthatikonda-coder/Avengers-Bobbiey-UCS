"""Regenerate docs/BUCS-Install-Guide.pdf: python tools/guide/shoot.py, then
python tools/guide/build_pdf.py docs/BUCS-Install-Guide.pdf  (needs reportlab, pillow, websockets, Chrome)."""
"""One-page BUCS install & activation guide (A4)."""
import sys
from pathlib import Path

from PIL import Image
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

HERE = Path(__file__).parent
OUT = Path(sys.argv[1])
F = r"C:\Windows\Fonts"
pdfmetrics.registerFont(TTFont("UI", rf"{F}\segoeui.ttf"))
pdfmetrics.registerFont(TTFont("UI-B", rf"{F}\segoeuib.ttf"))
pdfmetrics.registerFont(TTFont("UI-SB", rf"{F}\seguisb.ttf"))
pdfmetrics.registerFont(TTFont("Mono", rf"{F}\consola.ttf"))
pdfmetrics.registerFontFamily("UI", normal="UI", bold="UI-B")

INK, DIM, CY, VIO, NAVY = HexColor("#0b1f33"), HexColor("#4a6378"), HexColor("#0097b8"), HexColor("#7c5cd6"), HexColor("#04101c")
SITE = "saikiranthatikonda-coder.github.io/Avengers-Bobbiey-UCS"
W, H = A4
M = 30

c = canvas.Canvas(str(OUT), pagesize=A4)
c.setTitle("BUCS Pro: Install & Activate Guide")
c.setAuthor("Bobbiey · Avengers × Bobbiey Unified Command System")
c.setSubject("How to install BUCS, pay over UPI and activate your licence")

body = ParagraphStyle("b", fontName="UI", fontSize=8.6, leading=11.6, textColor=INK)
small = ParagraphStyle("s", parent=body, fontSize=7.6, leading=10, textColor=DIM)


def para(text, x, y_top, w, style=body):
    p = Paragraph(text, style)
    _, h = p.wrap(w, 1000)
    p.drawOn(c, x, y_top - h)
    return y_top - h


def step(n, title, x, y_top, w):
    c.setFillColor(VIO); c.setFont("UI-B", 15); c.drawString(x, y_top - 14, f"{n:02d}")
    c.setFillColor(INK); c.setFont("UI-B", 10.5); c.drawString(x + 26, y_top - 13, title)
    return y_top - 20


def shot(name, x, y_top, w, label=None):
    img = Image.open(HERE / f"{name}.png")
    h = w * img.height / img.width
    c.drawImage(ImageReader(img), x, y_top - h, w, h)
    c.setStrokeColor(HexColor("#c9d6e2")); c.setLineWidth(0.6); c.roundRect(x, y_top - h, w, h, 3, stroke=1, fill=0)
    if label:
        c.setFont("UI", 6.8); c.setFillColor(DIM); c.drawString(x, y_top - h - 9, label)
    return y_top - h - (12 if label else 4)


# ── header band ────────────────────────────────────────────────
c.setFillColor(NAVY); c.rect(0, H - 92, W, 92, stroke=0, fill=1)
c.setFillColor(CY); c.rect(0, H - 94, W, 2, stroke=0, fill=1)
c.setFillColor(HexColor("#9fdcff")); c.setFont("UI-SB", 7.5)
c.drawString(M, H - 28, "AVENGERS × BOBBIEY  ·  UNIFIED COMMAND SYSTEM")
c.setFillColor(white); c.setFont("UI-B", 19); c.drawString(M, H - 54, "BUCS Pro: Install & Activate")
c.setFillColor(HexColor("#b9cfe0")); c.setFont("UI", 9)
c.drawString(M, H - 72, "About 10 minutes, start to finish. You need a Windows 10/11 PC and any UPI app.")
c.setFillColor(HexColor("#b9cfe0")); c.setFont("UI-SB", 8.5)
c.drawRightString(W - M, H - 28, "₹149 / month  ·  ₹1,499 / 12 months")
c.setFillColor(HexColor("#6ee7ff")); c.setFont("UI-SB", 8)
url_w = c.stringWidth(SITE + "/#get", "UI-SB", 8)
c.drawRightString(W - M, H - 52, SITE + "/#get")
c.linkURL("https://" + SITE + "/#get", (W - M - url_w, H - 56, W - M, H - 44), relative=0)

# ── part A: install (steps 1–3) + screenshot ───────────────────
y = H - 110
colL, colR = M, M + 236
wL, wR = 222, W - M - colR
yl = step(1, "Install Python 3.11 or newer", colL, y, wL)
yl = para("Get it from <b>python.org/downloads</b>. On the first installer screen, tick "
          "<b>“Add python.exe to PATH”</b>, then click Install Now.", colL + 26, yl, wL - 26) - 9
yl = step(2, "Download BUCS and extract it", colL, yl, wL)
yl = para("On the website, click <b>DOWNLOAD BUCS (.ZIP)</b>. Right-click the ZIP → <b>Extract All</b>, "
          "for example into <b>Documents</b>. You get a folder named <font name='Mono'>Avengers-Bobbiey-UCS-main</font>.",
          colL + 26, yl, wL - 26) - 9
yl = step(3, "Double-click  Start BUCS.cmd", colL, yl, wL)
yl = para("It's inside that folder. The first launch sets everything up (3–5 minutes, keep the window open), adds a "
          "<b>BUCS</b> shortcut to your desktop, and opens BUCS in your browser.", colL + 26, yl, wL - 26) - 4
yl = para("If Windows shows <b>“Windows protected your PC”</b>, click <b>More info → Run anyway</b>. "
          "It appears for any new script downloaded from the internet.", colL + 26, yl, wL - 26, small)
yr = shot("web_get", colR, y + 2, wR, "The Get Started section on the website: download button and requirements.")
y = min(yl, yr) - 14

c.setStrokeColor(HexColor("#dfe8f0")); c.setLineWidth(0.8); c.line(M, y, W - M, y); y -= 12

# ── part B: activation screen → pay → activate ─────────────────
gap = 14
cw = (W - 2 * M - 2 * gap) / 3
x1, x2, x3 = M, M + cw + gap, M + 2 * (cw + gap)

t1 = step(4, "Copy your install ID", x1, y, cw)
t1 = para("BUCS opens on the activation screen. Click <b>COPY</b> next to your install ID "
          "(yours will differ from the example).", x1, t1, cw) - 6
b1 = shot("act", x1, t1, cw, "The activation screen (example install ID).")

t2 = step(5, "Pay with UPI and send details", x2, y, cw)
t2 = para("In the website's <b>Pricing</b> section, pick 1 or 12 months, scan the QR with GPay, PhonePe, Paytm or BHIM, "
          "then enter your <b>email</b>, the <b>UTR</b> (UPI transaction ID) and your <b>install ID</b> and press "
          "<b>SEND DETAILS</b>. Your email app opens; press send.", x2, t2, cw) - 6
b2 = shot("web_pay", x2, t2, cw, "The payment panel: QR, plan picker, details form.")

t3 = step(6, "Paste your key and ACTIVATE", x3, y, cw)
t3 = para("Your licence key arrives by email once the payment is confirmed (usually within a few hours). Paste it into "
          "<b>LICENCE KEY</b> on the activation screen and press <b>ACTIVATE</b>. BUCS starts immediately, with no restart.",
          x3, t3, cw) - 10

# help box in column 3
box_top = t3
c.setFillColor(HexColor("#f2f7fb")); c.setStrokeColor(HexColor("#cfe0ec"))
bh = 228
c.roundRect(x3, box_top - bh, cw, bh, 5, stroke=1, fill=1)
c.setFillColor(CY); c.setFont("UI-B", 8.2); c.drawString(x3 + 9, box_top - 16, "IF SOMETHING GETS STUCK")
tips = [
    ("“Python 3.11 or newer is required”", "Install Python (step 1), tick Add to PATH, then run Start BUCS again."),
    ("Setup stopped while installing", "Check your internet connection and run Start BUCS again. It retries cleanly."),
    ("Browser didn't open", "Open <b>http://127.0.0.1:8765</b> yourself while the BUCS window is running."),
    ("“Key belongs to another install”", "Send us the install ID shown on <i>this</i> PC; we'll reissue the key."),
    ("Closing BUCS", "Close the black BUCS window. Open it again from the desktop shortcut."),
]
ty = box_top - 24
for head, txt in tips:
    ty = para(f"<b>{head}</b><br/>{txt}", x3 + 9, ty, cw - 18, ParagraphStyle("t", parent=small, textColor=INK, leading=9.6, fontSize=7.4)) - 5

y = min(b1, b2, box_top - bh) - 12

# ── what's included ────────────────────────────────────────────
c.setFillColor(NAVY); band_h = 74
c.roundRect(M, y - band_h, W - 2 * M, band_h, 6, stroke=0, fill=1)
c.setFillColor(HexColor("#6ee7ff")); c.setFont("UI-B", 8.4); c.drawString(M + 14, y - 16, "WHAT'S INCLUDED IN BUCS PRO")
items = ["Command dashboard, HUD and 8-agent AI roster", "Voice control, conversation mode, webcam vision",
         "LLM switch: Claude, OpenAI, Gemini, Groq, local Ollama", "Google Calendar, Gmail and Tasks intelligence",
         "Threat intelligence, decision support, live telemetry", "Runs on your own PC: your data stays with you"]
colw = (W - 2 * M - 28) / 2
for i, it in enumerate(items):
    ix = M + 14 + (i % 2) * colw; iy = y - 32 - (i // 2) * 15
    c.setFillColor(HexColor("#00d9ff")); c.circle(ix + 3, iy + 3, 2.2, stroke=0, fill=1)
    c.setFillColor(HexColor("#dbeafe")); c.setFont("UI", 8.6); c.drawString(ix + 12, iy, it)
y = y - band_h - 8

# ── footer ─────────────────────────────────────────────────────
fy = 26
c.setStrokeColor(HexColor("#dfe8f0")); c.line(M, fy + 13, W - M, fy + 13)
c.setFillColor(INK); c.setFont("UI-B", 8.5); c.drawString(M, fy, "Need help?")
c.setFont("UI", 8.5); c.setFillColor(DIM)
c.drawString(M + 50, fy, "Email srisaikiranthatikonda@gmail.com with your install ID. We'll get you running.")
c.linkURL("mailto:srisaikiranthatikonda@gmail.com", (M + 80, fy - 3, M + 270, fy + 9), relative=0)
c.setFont("UI", 7); c.drawRightString(W - M, fy, "Built in Hyderabad · BUCS Pro")
if y < fy + 14:
    print("WARNING: content overlaps the footer by", fy + 14 - y, "pt")
c.showPage(); c.save()
print("wrote", OUT, "| lowest content y =", round(y, 1))
