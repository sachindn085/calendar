import os
import json
from flask import Flask, redirect, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import google.oauth2.credentials
from datetime import datetime, timedelta
from dotenv import load_dotenv
from dateutil.parser import parse as parse_date

load_dotenv()


app = Flask(__name__)
app.secret_key =os.getenv("SECRET_KEY")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tokens.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  


GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:5000/auth-receiver")
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.events"
]
CLIENT_CONFIG = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI]
    }
}


class UserCredentials(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(255), unique=True, nullable=False)
    token = db.Column(db.Text)
    refresh_token = db.Column(db.Text)
    token_uri = db.Column(db.Text)
    client_id = db.Column(db.Text)
    client_secret = db.Column(db.Text)
    scopes = db.Column(db.Text)

    def to_dict(self):
        return {
            "token": self.token,
            "refresh_token": self.refresh_token,
            "token_uri": self.token_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scopes": json.loads(self.scopes),
        }


def get_current_month_range():
    now = datetime.utcnow()
    time_min = datetime(now.year, now.month, 1)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)

    time_max = next_month - timedelta(seconds=1)
    return time_min.isoformat() + 'Z', time_max.isoformat() + 'Z'

def get_date_range(range_type):
    now = datetime.utcnow()

    if range_type == "today":
        start = datetime(now.year, now.month, now.day)
        end = start + timedelta(days=1) - timedelta(seconds=1)
    elif range_type == "this_week":
        start = now - timedelta(days=now.weekday())  # Monday
        start = datetime(start.year, start.month, start.day)
        end = start + timedelta(days=7) - timedelta(seconds=1)
    elif range_type == "this_month":
        start = datetime(now.year, now.month, 1)
        if now.month == 12:
            end = datetime(now.year + 1, 1, 1) - timedelta(seconds=1)
        else:
            end = datetime(now.year, now.month + 1, 1) - timedelta(seconds=1)
    else:
        try:
            parsed_date = parse_date(range_type)
            start = datetime(parsed_date.year, parsed_date.month, parsed_date.day)
            end = start + timedelta(days=1) - timedelta(seconds=1)
        except:
            # Default to this month if parsing fails
            start = datetime(now.year, now.month, 1)
            if now.month == 12:
                end = datetime(now.year + 1, 1, 1) - timedelta(seconds=1)
            else:
                end = datetime(now.year, now.month + 1, 1) - timedelta(seconds=1)

    return start.isoformat() + 'Z', end.isoformat() + 'Z'


def interpret_natural_query(query_text):
    """
    Convert natural query to one of 'today', 'this_week', 'this_month', or date string
    """
    query_text = query_text.lower().strip()

    if "today" in query_text:
        return "today"
    elif "week" in query_text:
        return "this_week"
    elif "month" in query_text:
        return "this_month"
    else:
        # Extract possible date
        match = re.search(r'\d{4}-\d{2}-\d{2}', query_text)
        if match:
            return match.group()
        return "this_month"



@app.route("/authorize")
def authorize():
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    return redirect(authorization_url)


@app.route("/auth-receiver")
def auth_receiver():
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials

    
    user_info_service = build('oauth2', 'v2', credentials=credentials)
    user_info = user_info_service.userinfo().get().execute()
    user_email = user_info['email']


    existing = UserCredentials.query.filter_by(user_email=user_email).first()
    if not existing:
        existing = UserCredentials(user_email=user_email)

    existing.token = credentials.token
    existing.refresh_token = credentials.refresh_token
    existing.token_uri = credentials.token_uri
    existing.client_id = credentials.client_id
    existing.client_secret = credentials.client_secret
    existing.scopes = json.dumps(credentials.scopes)

    db.session.add(existing)
    db.session.commit()

    return f"Authorization successful for {user_email}. You can now POST to /create_event?email={user_email}"


@app.route("/create_event", methods=["POST"])
def create_event():
    email = request.args.get("email")
    if not email:
        return jsonify({"error": "Missing email in query params"}), 400

    user_creds = UserCredentials.query.filter_by(user_email=email).first()
    if not user_creds:
        return redirect('/authorize')

    creds = google.oauth2.credentials.Credentials(**user_creds.to_dict())
    service = build('calendar', 'v3', credentials=creds)

    data = request.get_json()
    event = {
        "summary": data.get("summary", "Sample Event"),
        "location": data.get("location", ""),
        "description": data.get("description", ""),
        "start": {
            "dateTime": data.get("start"),
            "timeZone": "Asia/Kolkata"
        },
        "end": {
            "dateTime": data.get("end"),
            "timeZone": "Asia/Kolkata"
        }
    }

    created_event = service.events().insert(calendarId="primary", body=event).execute()
    return jsonify(created_event)

# @app.route("/fetch_events", methods=["GET"])
# def fetch_events():
#     email = request.args.get("email")
#     if not email:
#         return jsonify({"error": "Missing email in query params"}), 400

#     user_creds = UserCredentials.query.filter_by(user_email=email).first()
#     if not user_creds:
#         return redirect('/authorize')

#     creds = google.oauth2.credentials.Credentials(**user_creds.to_dict())
#     service = build('calendar', 'v3', credentials=creds)

#     time_min, time_max = get_current_month_range()

#     events_result = service.events().list(
#         calendarId='primary',
#         timeMin=time_min,
#         timeMax=time_max,
#         singleEvents=True,
#         orderBy='startTime'
#     ).execute()

#     events = events_result.get('items', [])
#     return jsonify(events)

@app.route("/fetch_events", methods=["GET"])
def fetch_events():
    email = request.args.get("email")
    user_query = request.args.get("query", "this month")  # Accept natural query

    if not email:
        return jsonify({"error": "Missing email in query params"}), 400

    user_creds = UserCredentials.query.filter_by(user_email=email).first()
    if not user_creds:
        return redirect('/authorize')

    creds = google.oauth2.credentials.Credentials(**user_creds.to_dict())
    service = build('calendar', 'v3', credentials=creds)

    range_type = interpret_natural_query(user_query)
    time_min, time_max = get_date_range(range_type)

    events_result = service.events().list(
        calendarId='primary',
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])
    return jsonify(events)

@app.route("/delete_event", methods=["DELETE"])
def delete_event():
    email = request.args.get("email")
    event_id = request.args.get("event_id")

    if not email or not event_id:
        return jsonify({"error": "Missing email or event_id in query params"}), 400

    user_creds = UserCredentials.query.filter_by(user_email=email).first()
    if not user_creds:
        return redirect('/authorize')

    creds = google.oauth2.credentials.Credentials(**user_creds.to_dict())
    service = build('calendar', 'v3', credentials=creds)

    try:
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return jsonify({"status": "deleted", "event_id": event_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    
@app.route("/update_event", methods=["PATCH"])
def update_event():
    email = request.args.get("email")
    event_id = request.args.get("event_id")

    if not email or not event_id:
        return jsonify({"error": "Missing email or event_id in query params"}), 400

    data = request.get_json()
    user_creds = UserCredentials.query.filter_by(user_email=email).first()
    if not user_creds:
        return redirect('/authorize')

    creds = google.oauth2.credentials.Credentials(**user_creds.to_dict())
    service = build('calendar', 'v3', credentials=creds)

    try:
        event = service.events().get(calendarId='primary', eventId=event_id).execute()

        
        if 'summary' in data: event['summary'] = data['summary']
        if 'location' in data: event['location'] = data['location']
        if 'description' in data: event['description'] = data['description']
        if 'start' in data: event['start']['dateTime'] = data['start']
        if 'end' in data: event['end']['dateTime'] = data['end']

        updated_event = service.events().update(calendarId='primary', eventId=event_id, body=event).execute()
        return jsonify(updated_event)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
