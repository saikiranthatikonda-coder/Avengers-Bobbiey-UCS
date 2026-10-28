"""Generate the website's UPI payment QR codes (one per plan, amount pre-filled).

The UPI ID and payee name are passed on the command line and are NOT stored in
the repo, so they never appear as text on the website. The QR images still
encode them (every UPI QR does: the payer's app shows the payee before paying).

    pip install "qrcode[pil]"
    python tools/make_upi_qr.py --vpa NAME@BANK --name "PAYEE NAME"

Writes docs/pay/bucs-1m.png and docs/pay/bucs-12m.png (+ the site/ mirror).
"""

import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import quote

import qrcode

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vpa", required=True)
    ap.add_argument("--name", required=True)
    a = ap.parse_args()
    cfg = json.loads((ROOT / "docs" / "payments.json").read_text(encoding="utf-8"))
    out = ROOT / "docs" / "pay"
    out.mkdir(exist_ok=True)
    for months, amount in cfg["prices"].items():
        term = "1 month" if months == "1" else f"{months} months"
        uri = (f"upi://pay?pa={quote(a.vpa, safe='@')}&pn={quote(a.name)}&am={amount}"
               f"&cu=INR&tn={quote('BUCS Pro ' + term)}")
        img = qrcode.make(uri, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
        path = out / f"bucs-{months}m.png"
        img.save(path)
        print("wrote", path.relative_to(ROOT), f"(₹{amount}, {term})")
    shutil.copytree(out, ROOT / "site" / "pay", dirs_exist_ok=True)


if __name__ == "__main__":
    main()
