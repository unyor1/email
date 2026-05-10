#!/usr/bin/env python3

import os
import ssl
import smtplib
import argparse
import requests
import traceback
import time
from pathlib import Path
from email.message import EmailMessage
from dotenv import load_dotenv


# ─── Configuration & Load Env ────────────────────────────────────────────────

def load_environment():
    """Loads the .env file from the current directory."""
    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        print("✓ Environment variables loaded from .env")
    else:
        print("! Warning: .env file not found. Ensure it is in the same directory.")


# ─── Email Logic ─────────────────────────────────────────────────────────────

def send_email_gmail(
    subject: str,
    body: str,
    gmail_user: str,
    gmail_app_password: str,
    to_email: str,
    from_email=None,
    dry_run: bool = False,
) -> None:
    if from_email is None:
        from_email = gmail_user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    if dry_run:
        print(f"[DRY RUN] Would send to {to_email}: {subject}")
        return

    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(gmail_user, gmail_app_password)
        server.send_message(msg)


# ─── Supabase Helpers ────────────────────────────────────────────────────────

def fetch_latest_sensor_state(supabase_url: str, service_role: str):
    hdrs = {
        "apikey": service_role,
        "Authorization": f"Bearer {service_role}",
    }
    endpoint = (
        f"{supabase_url.rstrip('/')}/rest/v1/sensor_logs"
        "?select=waterpump,pest,light,moisture,created_at"
        "&order=created_at.desc"
        "&limit=1"
    )
    resp = requests.get(endpoint, headers=hdrs, timeout=30)
    resp.raise_for_status()
    rows = resp.json()

    if not rows:
        return None

    row = rows[0]
    return {
        "waterpump": bool(row.get("waterpump")),
        "pest": bool(row.get("pest")),
        "light": float(row["light"]) if row.get("light") is not None else None,
        "moisture": float(row["moisture"]) if row.get("moisture") is not None else None,
        "created_at": row.get("created_at"),
    }


def fetch_approved_user_emails(supabase_url: str, service_role: str):
    hdrs = {
        "apikey": service_role,
        "Authorization": f"Bearer {service_role}",
    }
    endpoint = (
        f"{supabase_url.rstrip('/')}/rest/v1/user_profiles"
        "?select=email"
        "&role=eq.user"
    )
    resp = requests.get(endpoint, headers=hdrs, timeout=30)
    resp.raise_for_status()
    return [r.get("email") for r in resp.json() if r.get("email")]


def fetch_all_user_emails(supabase_url: str, service_role: str):
    return fetch_approved_user_emails(supabase_url, service_role)


# ─── Main Execution ──────────────────────────────────────────────────────────

def main() -> int:
    load_environment()

    parser = argparse.ArgumentParser(description="GreenPulse Supabase Alert System")
    parser.add_argument("--to", dest="to", help="Recipient email (overrides ALERT_TO env)")
    parser.add_argument("--gmail_user", help="Gmail address (overrides GMAIL_USER env)")
    parser.add_argument("--gmail_app_password", "--token", dest="gmail_app_password", help="Gmail app password")
    parser.add_argument("--from_email", help="From email header (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Don't send actual emails")
    parser.add_argument("--send-all", action="store_true", dest="send_all", help="Send to all Supabase users with role 'user'")
    parser.add_argument("--use_supabase", action="store_true", help="Fetch recipient email from Supabase (requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)")
    parser.add_argument("--supabase_url", help="Supabase URL (overrides SUPABASE_URL env)")
    parser.add_argument("--supabase_service_role_key", help="Supabase service role key")
    parser.add_argument("--supabase_email", help="If provided, use this Supabase user email instead of fetching latest")
    parser.add_argument("--supabase_user_id", help="Supabase user id (uid). If provided, fetch that user's email")
    parser.add_argument("--send-if-active", action="store_true", dest="send_if_active", help="Send email if latest Supabase sensor_logs show the waterpump or pest is on")
    parser.add_argument("--check-waterpump", action="store_true", dest="check_waterpump", help="Send email if latest Supabase sensor_logs show the waterpump is on")
    parser.add_argument("--check-pest", action="store_true", dest="check_pest", help="Send email if latest Supabase sensor_logs show the pest control is on")
    parser.add_argument("--check-light", "--check-light-low", action="store_true", dest="check_light_low", help="Send email if latest Supabase sensor_logs show light is below threshold")
    parser.add_argument("--check-soil", "--check-soil-low", action="store_true", dest="check_soil_low", help="Send email if latest Supabase sensor_logs show soil moisture is below threshold")
    parser.add_argument("--sensor-threshold", type=float, default=20.0, help="Threshold for low light and low soil moisture checks (default 20)")
    args = parser.parse_args()

    if args.send_all:
        args.use_supabase = True

    if not (args.send_if_active or args.check_waterpump or args.check_pest or args.check_light_low or args.check_soil_low):
        args.send_if_active = True
        args.check_waterpump = True
        args.check_pest = True
        args.check_light_low = True
        args.check_soil_low = True

    active_check_mode = args.send_if_active or args.check_waterpump or args.check_pest or args.check_light_low or args.check_soil_low

    gmail_user = args.gmail_user or os.getenv("GMAIL_USER")
    gmail_app_password = args.gmail_app_password or os.getenv("GMAIL_APP_PASSWORD")
    supabase_url = args.supabase_url or os.getenv("SUPABASE_URL")
    service_role = args.supabase_service_role_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    to_email = args.to or os.getenv("ALERT_TO")

    if not active_check_mode:
        print("ERROR: Please specify at least one active check mode: --send-if-active, --check-waterpump, --check-pest, --check-light, or --check-soil")
        return 1

    if not gmail_user or not gmail_app_password or (not to_email and not args.use_supabase and not args.send_all):
        print("ERROR: Missing required Gmail credentials or recipient. Provide:")
        print("  GMAIL_USER, GMAIL_APP_PASSWORD, ALERT_TO (or --to), or use --use_supabase")
        return 1

    if (active_check_mode or args.use_supabase) and (not supabase_url or not service_role):
        print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY for Supabase lookup")
        return 1

    try:
        # Determine recipient(s)
        if args.use_supabase:
            if args.supabase_email:
                to_email = args.supabase_email
            elif args.supabase_user_id:
                try:
                    hdrs = {"apikey": service_role, "Authorization": f"Bearer {service_role}"}
                    user_endpoint = f"{supabase_url.rstrip('/')}/auth/v1/admin/users/{args.supabase_user_id}"
                    resp = requests.get(user_endpoint, headers=hdrs, timeout=30)
                    resp.raise_for_status()
                    candidate = resp.json()
                    to_email = candidate.get("email") if isinstance(candidate, dict) else None
                    if not to_email:
                        print("Supabase user has no email or could not be found")
                        return 1
                    print(f"Using Supabase user email: {to_email}")
                except Exception as e:
                    print("Error fetching user from Supabase:", e)
                    traceback.print_exc()
                    return 1
            else:
                try:
                    hdrs = {"apikey": service_role, "Authorization": f"Bearer {service_role}"}
                    users_endpoint = f"{supabase_url.rstrip('/')}/auth/v1/admin/users"
                    resp = requests.get(users_endpoint, headers=hdrs, timeout=30)
                    resp.raise_for_status()
                    users = resp.json()
                    if isinstance(users, dict) and "users" in users:
                        users_list = users["users"]
                    elif isinstance(users, list):
                        users_list = users
                    else:
                        users_list = []

                    if not users_list:
                        print("No users found in Supabase")
                        return 1

                    users_list.sort(key=lambda u: u.get("created_at"), reverse=True)

                    if args.send_all:
                        try:
                            emails = fetch_all_user_emails(supabase_url, service_role)
                        except Exception as e:
                            print("Error fetching approved user emails:", e)
                            traceback.print_exc()
                            return 1
                        if not emails:
                            print("No user emails available to send to")
                            return 1
                        # keep to_email None here — we iterate emails later
                    else:
                        candidate = users_list[0]
                        to_email = candidate.get("email")
                        if not to_email:
                            print("Latest Supabase user has no email field")
                            return 1
                        print(f"Using Supabase user email: {to_email}")
                except Exception as e:
                    print("Error fetching users from Supabase:", e)
                    traceback.print_exc()
                    return 1

        # Monitoring loop
        CHECK_INTERVAL_SECONDS = 5
        last_state = None  # used to detect off->on transitions

        while True:
            try:
                latest_state = fetch_latest_sensor_state(supabase_url, service_role)
            except Exception as e:
                print("Error fetching latest switch state:", e)
                traceback.print_exc()
                return 1

            if not latest_state:
                print("No switch state found in Supabase.")
                return 0

            print(
                "Current state: "
                f"Water Pump={'ON' if latest_state['waterpump'] else 'OFF'} | "
                f"Pest={'ON' if latest_state['pest'] else 'OFF'} | "
                f"Light={latest_state.get('light') if latest_state.get('light') is not None else 'N/A'} | "
                f"Soil Moisture={latest_state.get('moisture') if latest_state.get('moisture') is not None else 'N/A'}"
            )

            # On first iteration, record and don't send alerts
            if last_state is None:
                last_state = latest_state
                print("Initial state recorded — waiting for changes before sending alerts.")
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            new_events = []

            # Water pump edge trigger (off -> on)
            if args.send_if_active or args.check_waterpump:
                was_on = bool(last_state.get("waterpump"))
                is_on = bool(latest_state.get("waterpump"))
                if is_on and not was_on:
                    new_events.append(("waterpump", {
                        "subject": "Water Pump Alert",
                        "body": (
                            "Water Pump was turned ON.\n\n"
                            f"Water Pump: ON\n"
                            f"Pest Control: {'ON' if latest_state['pest'] else 'OFF'}\n"
                            f"Light: {latest_state.get('light') if latest_state.get('light') is not None else 'N/A'}\n"
                            f"Soil Moisture: {latest_state.get('moisture') if latest_state.get('moisture') is not None else 'N/A'}\n"
                            f"Reported at: {latest_state.get('created_at')}\n"
                        ),
                    }))

            # Pest control edge trigger
            if args.send_if_active or args.check_pest:
                was_on = bool(last_state.get("pest"))
                is_on = bool(latest_state.get("pest"))
                if is_on and not was_on:
                    new_events.append(("pest", {
                        "subject": "Pest Control Alert",
                        "body": (
                            "Pest Control was turned ON.\n\n"
                            f"Water Pump: {'ON' if latest_state['waterpump'] else 'OFF'}\n"
                            f"Pest Control: ON\n"
                            f"Light: {latest_state.get('light') if latest_state.get('light') is not None else 'N/A'}\n"
                            f"Soil Moisture: {latest_state.get('moisture') if latest_state.get('moisture') is not None else 'N/A'}\n"
                            f"Reported at: {latest_state.get('created_at')}\n"
                        ),
                    }))

            # Low light: only trigger between 06:00 and 17:00 local time
            if args.check_light_low:
                light = latest_state.get("light")
                if light is None:
                    print("No light value available in Supabase.")
                else:
                    from datetime import datetime

                    now_hour = datetime.now().hour
                    if 6 <= now_hour <= 17:
                        was_low = (last_state.get("light") is not None) and (float(last_state.get("light")) < args.sensor_threshold)
                        is_low = float(light) < args.sensor_threshold
                        if is_low and not was_low:
                            new_events.append(("light", {
                                "subject": "Low Light Alert",
                                "body": (
                                    "Light intensity is below threshold. Move to better direct sunlight.\n\n"
                                    f"Light: {light}\n"
                                    f"Soil Moisture: {latest_state.get('moisture') if latest_state.get('moisture') is not None else 'N/A'}\n"
                                    f"Water Pump: {'ON' if latest_state['waterpump'] else 'OFF'}\n"
                                    f"Pest Control: {'ON' if latest_state['pest'] else 'OFF'}\n"
                                    f"Reported at: {latest_state.get('created_at')}\n"
                                ),
                            }))
                    else:
                        print("Outside light alert hours (06:00-17:00). Skipping light check.")

            # Low soil moisture edge trigger
            if args.check_soil_low:
                moisture = latest_state.get("moisture")
                if moisture is None:
                    print("No soil moisture value available in Supabase.")
                else:
                    was_low = (last_state.get("moisture") is not None) and (float(last_state.get("moisture")) < args.sensor_threshold)
                    is_low = float(moisture) < args.sensor_threshold
                    if is_low and not was_low:
                        new_events.append(("soil", {
                            "subject": "Low Soil Moisture Alert",
                            "body": (
                                "Soil moisture is below threshold. Turn on the pump.\n\n"
                                f"Soil Moisture: {moisture}\n"
                                f"Light: {latest_state.get('light') if latest_state.get('light') is not None else 'N/A'}\n"
                                f"Water Pump: {'ON' if latest_state['waterpump'] else 'OFF'}\n"
                                f"Pest Control: {'ON' if latest_state['pest'] else 'OFF'}\n"
                                f"Reported at: {latest_state.get('created_at')}\n"
                            ),
                        }))

            if not new_events:
                print("No new alerts at this pass.")
            else:
                if args.use_supabase and args.send_all:
                    try:
                        emails = fetch_all_user_emails(supabase_url, service_role)
                    except Exception as e:
                        print("Error fetching approved user emails:", e)
                        traceback.print_exc()
                        return 1
                    if not emails:
                        print("No user emails available to send to")
                        return 1
                    for key, event in new_events:
                        for email in emails:
                            try:
                                send_email_gmail(
                                    subject=event["subject"],
                                    body=event["body"],
                                    gmail_user=gmail_user,
                                    gmail_app_password=gmail_app_password,
                                    to_email=email,
                                    from_email=args.from_email,
                                    dry_run=args.dry_run,
                                )
                                print(f"Sent {event['subject']} to {email}")
                            except Exception as ex:
                                print(f"Failed to send {event['subject']} to {email}: {ex}")
                else:
                    if not to_email:
                        print("ERROR: No recipient email available for alert.")
                        return 1
                    for key, event in new_events:
                        send_email_gmail(
                            subject=event["subject"],
                            body=event["body"],
                            gmail_user=gmail_user,
                            gmail_app_password=gmail_app_password,
                            to_email=to_email,
                            from_email=args.from_email,
                            dry_run=args.dry_run,
                        )
                        print(f"Email sent to {to_email} ({event['subject']})")

            last_state = latest_state

            time.sleep(CHECK_INTERVAL_SECONDS)

    except Exception as e:
        print(f"Critical Error: {e}")
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
