

import os
import re
import sqlite3
import logging
from datetime import datetime
from flask import (
    Flask, render_template, request,
    redirect, url_for, jsonify, session
)
import requests

app = Flask(__name__)

# Security settings
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "local-development-key-change-before-deployment"
)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

logging.basicConfig(level=logging.INFO)


# -----------------------------------------
# DATABASE CONNECTION
# -----------------------------------------

def get_db_connection():
    conn = sqlite3.connect("foodrescue.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# -----------------------------------------
# INPUT VALIDATION HELPERS
# -----------------------------------------

def clean_text(value, max_length=200):
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]


def valid_contact(contact):
    return bool(re.fullmatch(r"\d{10}", contact))


# -----------------------------------------
# CREATE DATABASE TABLES
# -----------------------------------------

def create_table():
    conn = get_db_connection()

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS donations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donor_name TEXT NOT NULL,
                food_name TEXT NOT NULL,
                category TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit TEXT NOT NULL,
                prepared_at TEXT NOT NULL,
                deadline TEXT NOT NULL,
                storage TEXT NOT NULL,
                location TEXT NOT NULL,
                contact TEXT NOT NULL,
                description TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donation_id INTEGER NOT NULL,
                recipient_name TEXT NOT NULL,
                organization TEXT,
                contact TEXT NOT NULL,
                requested_quantity INTEGER NOT NULL,
                purpose TEXT,
                status TEXT DEFAULT 'Pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (donation_id) REFERENCES donations(id)
            )
        """)

        conn.commit()

    finally:
        conn.close()


# -----------------------------------------
# RETRIEVE REAL FOOD DONATIONS
# -----------------------------------------

def retrieve_donations(query):

    conn = get_db_connection()

    try:
        donations = conn.execute("""
            SELECT food_name, category, quantity, unit,
                   location, deadline, storage, description
            FROM donations
            WHERE datetime(deadline) > datetime('now', 'localtime')
            ORDER BY id DESC
            LIMIT 20
        """).fetchall()

    finally:
        conn.close()

    query_words = set(query.lower().split())
    results = []

    for donation in donations:

        donation_text = (
            str(donation["food_name"]) + " " +
            str(donation["category"]) + " " +
            str(donation["location"]) + " " +
            str(donation["description"] or "")
        ).lower()

        score = sum(
            1 for word in query_words
            if len(word) > 2 and word in donation_text
        )

        if score > 0:
            results.append((score, donation))

    results.sort(key=lambda item: item[0], reverse=True)

    return "\n".join(
        f"Food: {d['food_name']}, "
        f"Category: {d['category']}, "
        f"Quantity: {d['quantity']} {d['unit']}, "
        f"Location: {d['location']}, "
        f"Deadline: {d['deadline']}, "
        f"Storage: {d['storage']}"
        for score, d in results[:5]
    )


# -----------------------------------------
# RETRIEVE KNOWLEDGE BASE INFORMATION
# -----------------------------------------

def retrieve_knowledge(query):

    try:
        with open("knowledge_base.txt", "r", encoding="utf-8") as file:
            content = file.read()

    except FileNotFoundError:
        logging.error("knowledge_base.txt was not found.")
        return ""

    sections = [
        section.strip()
        for section in content.split("\n\n")
        if section.strip()
    ]

    stop_words = {
        "the", "is", "a", "an", "and", "or", "to",
        "of", "in", "for", "how", "what", "can",
        "i", "you", "my", "do", "does", "are"
    }

    query_words = {
        word.strip(".,?!:;").lower()
        for word in query.split()
    } - stop_words

    scored_sections = []

    for section in sections:

        section_words = {
            word.strip(".,?!:;").lower()
            for word in section.split()
        }

        score = len(query_words.intersection(section_words))

        if score > 0:
            scored_sections.append((score, section))

    scored_sections.sort(
        reverse=True,
        key=lambda item: item[0]
    )

    return "\n\n".join(
        section for score, section in scored_sections[:3]
    )


# -----------------------------------------
# RETRIEVE FOOD REQUEST STATUS
# -----------------------------------------

def retrieve_request_status(contact):

    conn = get_db_connection()

    try:
        requests_data = conn.execute("""
            SELECT
                food_requests.id,
                food_requests.recipient_name,
                food_requests.requested_quantity,
                food_requests.status,
                food_requests.created_at,
                donations.food_name
            FROM food_requests
            JOIN donations
            ON food_requests.donation_id = donations.id
            WHERE food_requests.contact = ?
            ORDER BY food_requests.id DESC
        """, (contact,)).fetchall()

    finally:
        conn.close()

    if not requests_data:
        return "No food request was found for this contact number."

    results = []

    for item in requests_data:
        results.append(
            f"Request ID: {item['id']}, "
            f"Food: {item['food_name']}, "
            f"Quantity: {item['requested_quantity']}, "
            f"Status: {item['status']}, "
            f"Date: {item['created_at']}"
        )

    return "\n".join(results)


# -----------------------------------------
# EXTRACT CONTACT NUMBER
# -----------------------------------------

def extract_contact_number(message):

    numbers = re.findall(r"\b\d{10}\b", message)

    return numbers[0] if numbers else None


# -----------------------------------------
# RESCUEBOT AI RESPONSE
# -----------------------------------------

def get_ai_response(message):

    knowledge = retrieve_knowledge(message)
    donation_info = retrieve_donations(message)

    if not knowledge and not donation_info:
        return (
            "I can help with food donations, food requests, "
            "smart matching, food safety and reducing food waste. "
            "Please ask me something related to FoodRescue AI."
        )

    url = "http://localhost:11434/api/generate"

    prompt = f"""
    You are RescueBot, the AI assistant of FoodRescue AI.

    Answer the user's question using the knowledge provided below.

    KNOWLEDGE BASE:
    {knowledge}

    AVAILABLE FOOD DONATIONS:
    {donation_info if donation_info else "No matching donation records found."}

    RULES:
    - Answer in simple and friendly language.
    - Focus only on FoodRescue AI and food rescue topics.
    - Use actual donation records for availability questions.
    - Mention exact food name, category, quantity, location and deadline when available.
    - Never invent donation details.
    - If no matching donation is found, clearly say so.
    - Do not confuse cooked food with raw vegetables or fruits.
    - Explain how users can submit a food request.
    - If information is unavailable, say so politely.

    USER QUESTION:
    {message}

    ANSWER:
    """

    data = {
        "model": "llama3.2:3b",
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(
            url,
            json=data,
            timeout=120
        )

        response.raise_for_status()

        result = response.json()

        return result.get(
            "response",
            "Sorry, I could not generate an answer."
        )

    except requests.RequestException as error:
        logging.error("Ollama connection error: %s", error)

        return (
            "Sorry, RescueBot is temporarily unavailable. "
            "Please check whether Ollama is running and try again."
        )

    except (ValueError, KeyError) as error:
        logging.error("Invalid Ollama response: %s", error)

        return "Sorry, I could not process the AI response."


# -----------------------------------------
# HOME PAGE
# -----------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


# -----------------------------------------
# CHATBOT PAGE
# -----------------------------------------

@app.route("/chatbot")
def chatbot():
    return render_template("chatbot.html")


# -----------------------------------------
# CHATBOT API
# -----------------------------------------

@app.route("/chat", methods=["POST"])
def chat():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "reply": "Invalid message format."
        }), 400

    message = clean_text(data.get("message", ""), 1000)

    if not message:
        return jsonify({
            "reply": "Please enter a message."
        }), 400

    status_keywords = [
        "request status",
        "my request",
        "approved",
        "pending",
        "rejected"
    ]

    wants_status = any(
        word in message.lower()
        for word in status_keywords
    )

    if wants_status or session.get("awaiting_request_contact"):

        contact = extract_contact_number(message)

        if contact:
            session.pop("awaiting_request_contact", None)
            reply = retrieve_request_status(contact)

        else:
            session["awaiting_request_contact"] = True

            reply = (
                "Please enter your registered 10-digit contact number "
                "so I can check your food request status."
            )

        return jsonify({"reply": reply})

    reply = get_ai_response(message)

    return jsonify({"reply": reply})


# -----------------------------------------
# DONATE FOOD PAGE
# -----------------------------------------

@app.route("/donate")
def donate():
    return render_template("donate.html")


# -----------------------------------------
# AVAILABLE FOOD LISTINGS
# -----------------------------------------

@app.route("/listings")
def listings():

    conn = get_db_connection()

    try:
        donations = conn.execute("""
            SELECT *
            FROM donations
            ORDER BY id DESC
        """).fetchall()

    finally:
        conn.close()

    return render_template(
        "listings.html",
        donations=donations
    )


# -----------------------------------------
# SMART MATCHING
# -----------------------------------------

@app.route("/matching", methods=["GET", "POST"])
def smart_matching():

    conn = get_db_connection()

    matches = []
    searched = False

    try:

        if request.method == "POST":

            searched = True

            category = clean_text(
                request.form.get("category", ""),
                100
            )

            location = clean_text(
                request.form.get("location", ""),
                150
            )

            quantity_input = request.form.get(
                "quantity", ""
            ).strip()

            try:
                quantity = int(quantity_input)

                if quantity <= 0 or not category or not location:
                    raise ValueError

            except (ValueError, TypeError):
                return "Please enter valid matching requirements.", 400

            matches = conn.execute("""
                SELECT *
                FROM donations
                WHERE LOWER(TRIM(category)) = LOWER(?)
                AND quantity >= ?
                AND LOWER(TRIM(location)) LIKE LOWER(?)
                AND datetime(deadline) > datetime('now', 'localtime')
            """, (
                category,
                quantity,
                "%" + location + "%"
            )).fetchall()

            now = datetime.now()
            scored_matches = []

            for donation in matches:

                score = 90

                try:
                    deadline = datetime.fromisoformat(
                        donation["deadline"]
                    )

                    hours_left = (
                        deadline - now
                    ).total_seconds() / 3600

                    if hours_left <= 6:
                        score += 10
                    elif hours_left <= 24:
                        score += 8
                    elif hours_left <= 48:
                        score += 5
                    else:
                        score += 2

                except (ValueError, TypeError):
                    pass

                donation_data = dict(donation)

                donation_data["match_score"] = min(
                    score, 100
                )

                scored_matches.append(donation_data)

            matches = sorted(
                scored_matches,
                key=lambda item: item["match_score"],
                reverse=True
            )

    finally:
        conn.close()

    return render_template(
        "matching.html",
        matches=matches,
        searched=searched
    )


# -----------------------------------------
# MANAGE FOOD REQUESTS
# -----------------------------------------

@app.route("/requests")
def view_requests():

    conn = get_db_connection()

    try:
        requests_data = conn.execute("""
            SELECT
                food_requests.*,
                donations.food_name
            FROM food_requests
            JOIN donations
            ON food_requests.donation_id = donations.id
            ORDER BY food_requests.id DESC
        """).fetchall()

    finally:
        conn.close()

    return render_template(
        "requests.html",
        requests=requests_data
    )


# -----------------------------------------
# APPROVE OR REJECT FOOD REQUEST
# -----------------------------------------

@app.route(
    "/update-request/<int:request_id>",
    methods=["POST"]
)
def update_request(request_id):

    status = request.form.get("status")

    if status not in ["Approved", "Rejected"]:
        return "Invalid request status", 400

    conn = get_db_connection()

    try:
        cursor = conn.execute("""
            UPDATE food_requests
            SET status = ?
            WHERE id = ?
        """, (status, request_id))

        conn.commit()

        if cursor.rowcount == 0:
            return "Food request not found", 404

    finally:
        conn.close()

    return redirect(url_for("view_requests"))


# -----------------------------------------
# OPEN FOOD REQUEST PAGE
# -----------------------------------------

@app.route("/request/<int:donation_id>")
def request_food(donation_id):

    conn = get_db_connection()

    try:
        donation = conn.execute(
            "SELECT * FROM donations WHERE id = ?",
            (donation_id,)
        ).fetchone()

    finally:
        conn.close()

    if donation is None:
        return "Food donation not found", 404

    return render_template(
        "request.html",
        donation_id=donation_id,
        donation=donation
    )


# -----------------------------------------
# SUBMIT FOOD REQUEST
# -----------------------------------------

@app.route(
    "/submit-request/<int:donation_id>",
    methods=["POST"]
)
def submit_request(donation_id):

    recipient_name = clean_text(
        request.form.get("recipient_name", ""),
        100
    )

    organization = clean_text(
        request.form.get("organization", ""),
        150
    )

    contact = request.form.get("contact", "").strip()

    purpose = clean_text(
        request.form.get("purpose", ""),
        500
    )

    try:
        requested_quantity = int(
            request.form.get("requested_quantity", "")
        )

    except (ValueError, TypeError):
        return "Please enter a valid quantity.", 400

    if not recipient_name:
        return "Recipient name is required.", 400

    if not valid_contact(contact):
        return "Please enter a valid 10-digit contact number.", 400

    if requested_quantity <= 0:
        return "Quantity must be greater than zero.", 400

    conn = get_db_connection()

    try:
        donation = conn.execute(
            "SELECT * FROM donations WHERE id = ?",
            (donation_id,)
        ).fetchone()

        if donation is None:
            return "Food donation not found", 404

        if requested_quantity > donation["quantity"]:
            return (
                "Please enter a valid quantity within "
                "the available amount."
            ), 400

        conn.execute("""
            INSERT INTO food_requests (
                donation_id,
                recipient_name,
                organization,
                contact,
                requested_quantity,
                purpose
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            donation_id,
            recipient_name,
            organization,
            contact,
            requested_quantity,
            purpose
        ))

        conn.commit()

    finally:
        conn.close()

    return """
        <h2>Food Request Submitted Successfully!</h2>
        <p>Your request is pending approval from the donor.</p>
        <a href="/listings">Back to Food Listings</a>
    """


# -----------------------------------------
# SUBMIT FOOD DONATION
# -----------------------------------------

@app.route("/submit-donation", methods=["POST"])
def submit_donation():

    donor_name = clean_text(
        request.form.get("donor_name", ""),
        100
    )

    food_name = clean_text(
        request.form.get("food_name", ""),
        100
    )

    category = clean_text(
        request.form.get("category", ""),
        100
    )

    unit = clean_text(
        request.form.get("unit", ""),
        30
    )

    prepared_at = clean_text(
        request.form.get("prepared_at", ""),
        30
    )

    deadline = clean_text(
        request.form.get("deadline", ""),
        30
    )

    storage = clean_text(
        request.form.get("storage", ""),
        100
    )

    location = clean_text(
        request.form.get("location", ""),
        150
    )

    contact = request.form.get("contact", "").strip()

    description = clean_text(
        request.form.get("description", ""),
        500
    )

    try:
        quantity = int(request.form.get("quantity", ""))

    except (ValueError, TypeError):
        return "Please enter a valid food quantity.", 400

    required_fields = [
        donor_name,
        food_name,
        category,
        unit,
        prepared_at,
        deadline,
        storage,
        location
    ]

    if not all(required_fields):
        return "Please complete all required fields.", 400

    if not valid_contact(contact):
        return "Please enter a valid 10-digit contact number.", 400

    if quantity <= 0:
        return "Quantity must be greater than zero.", 400

    try:
        prepared_date = datetime.fromisoformat(prepared_at)
        deadline_date = datetime.fromisoformat(deadline)

        if deadline_date <= prepared_date:
            return "Collection deadline must be after preparation time.", 400

    except ValueError:
        return "Please enter valid preparation and deadline dates.", 400

    conn = get_db_connection()

    try:
        conn.execute("""
            INSERT INTO donations (
                donor_name,
                food_name,
                category,
                quantity,
                unit,
                prepared_at,
                deadline,
                storage,
                location,
                contact,
                description
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            donor_name,
            food_name,
            category,
            quantity,
            unit,
            prepared_at,
            deadline,
            storage,
            location,
            contact,
            description
        ))

        conn.commit()

    finally:
        conn.close()

    return """
        <h2>Thank you for donating!</h2>
        <p>Your food donation has been saved successfully.</p>
        <a href="/">Return to Home</a>
        <br>
        <a href="/listings">View Food Listings</a>
    """


# -----------------------------------------
# APPLICATION START
# -----------------------------------------

if __name__ == "__main__":

    create_table()

    debug_mode = os.environ.get(
        "FLASK_DEBUG", "0"
    ) == "1"

    app.run(
        debug=debug_mode,
        host="127.0.0.1",
        port=5000
    )
