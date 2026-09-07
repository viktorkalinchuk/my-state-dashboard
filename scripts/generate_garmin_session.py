#!/usr/bin/env python3
"""
ONE-TIME, LOCAL-ONLY script. Run this yourself, on your own machine, once.

It logs in to Garmin Connect interactively (prompts for email/password/MFA
code right here in your terminal — nothing is sent anywhere else, and this
script does not talk to Claude or any third party) and saves a reusable
session/token directory. That token directory is what the daily GitHub
Actions workflow uses to authenticate — so your Garmin password itself
never has to be stored anywhere, including as a GitHub secret.

Usage:
    pip install garminconnect
    python3 scripts/generate_garmin_session.py

Then follow the printed instructions to pack the resulting ~/.garminconnect
directory into a GitHub secret.
"""

import base64
import getpass
import io
import os
import tarfile

from garminconnect import Garmin

TOKEN_DIR = os.path.expanduser("~/.garminconnect")


def main():
    print("Garmin Connect login (one-time, local only).")
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")

    garmin = Garmin(email=email, password=password, is_cn=False)
    garmin.login()  # will prompt for an MFA code here if your account uses it
    garmin.garth.dump(TOKEN_DIR)
    print(f"\nSaved session tokens to {TOKEN_DIR}")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(TOKEN_DIR, arcname=".garminconnect")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")

    out_path = os.path.join(os.getcwd(), "garmin_session_secret.b64")
    with open(out_path, "w") as f:
        f.write(encoded)

    print(f"""
Wrote a base64-encoded, gzip-tarred copy of your session to:
    {out_path}

Next steps:
  1. On GitHub: your repo -> Settings -> Secrets and variables -> Actions
     -> New repository secret.
  2. Name: GARMIN_SESSION_B64
  3. Value: paste the entire contents of {out_path}
  4. Delete {out_path} from your machine afterwards (it's a live session —
     treat it like a password). It is NOT committed to git.

This session/token typically stays valid for months without needing to
log in again. If the daily workflow ever starts failing with an auth
error, just re-run this script and update the secret.
""")


if __name__ == "__main__":
    main()
