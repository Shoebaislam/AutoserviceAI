"""AutoService AI 2.0 — technician-approved vehicle inspection reporting.

Local Streamlit -> faster-whisper -> technician review -> n8n webhook.
The n8n workflow owns email delivery and report status persistence.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
import streamlit as st
from faster_whisper import WhisperModel

st.set_page_config(page_title="AutoService AI 2.0", page_icon="🚗", layout="wide")
st.title("🚗 AutoService AI 2.0")
st.caption("Technician audio → transcription → reviewed inspection report → n8n automation")


@st.cache_resource(show_spinner="Loading local speech recognition model...")
def load_whisper() -> WhisperModel:
    # CPU-friendly setting for local Windows development.
    return WhisperModel("tiny.en", device="cpu", compute_type="int8")


def transcribe_audio(uploaded_file) -> str:
    suffix = Path(uploaded_file.name).suffix.lower() or ".wav"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(uploaded_file.getvalue())
            temp_path = temp_file.name
        segments, _info = load_whisper().transcribe(
            temp_path,
            beam_size=5,
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def extract_inspection_draft(transcript: str) -> dict[str, str]:
    """Conservative, rule-based draft. Technician must verify every field."""
    text = transcript.strip()
    lower = text.lower()

    # These are *draft labels*, not independent diagnostic conclusions.
    complaint_terms = {
        "reduced range": "Reported reduction in driving range",
        "range drop": "Reported reduction in driving range",
        "battery drain": "Reported excessive battery drain",
        "not charging": "Reported charging problem",
        "charging issue": "Reported charging problem",
        "overheat": "Reported overheating concern",
        "warning light": "Reported vehicle warning indicator",
        "noise": "Reported unusual noise",
        "vibration": "Reported vehicle vibration",
    }
    complaint = next(
        (description for term, description in complaint_terms.items() if term in lower),
        "Review transcript and enter the customer complaint",
    )

    # Retain the technician's words instead of inventing measurements or diagnoses.
    observation = text or "Review transcript and enter technician observations"

    if any(term in lower for term in ("battery", "bms", "range", "charging")):
        recommendation = "Review battery/BMS diagnostic logs and verify relevant measurements"
    elif any(term in lower for term in ("overheat", "cooling", "temperature")):
        recommendation = "Inspect the cooling system and verify temperature readings"
    else:
        recommendation = "Review inspection findings and confirm the appropriate service action"

    return {
        "complaint": complaint,
        "observation": observation,
        "recommendation": recommendation,
    }


def new_report_id() -> str:
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"EV-{date_part}-{uuid.uuid4().hex[:8].upper()}"


def submit_to_n8n(report: dict) -> requests.Response:
    url = str(st.secrets.get("N8N_WEBHOOK_URL", "")).strip()
    if not url:
        raise ValueError("N8N_WEBHOOK_URL is missing from .streamlit/secrets.toml")
    if not url.startswith(("http://", "https://")):
        raise ValueError("N8N_WEBHOOK_URL must be an HTTP(S) URL")
    response = requests.post(url, json=report, timeout=30)
    response.raise_for_status()
    return response


if "report" not in st.session_state:
    st.session_state.report = None
if "submitted_id" not in st.session_state:
    st.session_state.submitted_id = None

st.subheader("1. Upload technician recording")
audio_file = st.file_uploader(
    "Choose a recording (WAV, MP3, M4A, or MP4)",
    type=["wav", "mp3", "m4a", "mp4"],
)
if audio_file is not None:
    st.audio(audio_file)

if st.button("Transcribe and prepare inspection draft", disabled=audio_file is None):
    try:
        with st.spinner("Transcribing technician audio..."):
            transcript = transcribe_audio(audio_file)
        if not transcript:
            st.error("No speech was detected. Please try a clearer recording.")
        else:
            draft = extract_inspection_draft(transcript)
            st.session_state.report = {
                "report_id": new_report_id(),
                "transcript": transcript,
                **draft,
            }
            st.session_state.submitted_id = None
            st.success("Draft ready. Please review and correct all details before approval.")
    except Exception as exc:
        st.error(f"Transcription failed: {exc}")

report = st.session_state.report
if report is not None:
    st.divider()
    st.subheader("2. Technician review")
    st.info("This is an AI-assisted draft, not a verified vehicle diagnosis. Check the transcript and findings.")

    with st.form("inspection_review"):
        report_id = st.text_input("Report ID", value=report["report_id"], disabled=True)
        customer_email = st.text_input("Customer email (use your own address for testing)")
        vehicle_type = st.selectbox(
            "Vehicle type", ["Electric Vehicle", "Hybrid Vehicle", "Gasoline/Diesel Vehicle"]
        )
        transcript = st.text_area("Transcript (editable)", value=report["transcript"], height=160)
        complaint = st.text_area("Customer complaint", value=report["complaint"], height=90)
        observation = st.text_area("Technician observation", value=report["observation"], height=140)
        recommendation = st.text_area("Recommended action", value=report["recommendation"], height=100)
        approved = st.checkbox("I have reviewed and approved this inspection report")
        submit = st.form_submit_button(
            "Approve and send to n8n", type="primary",
            disabled=st.session_state.submitted_id == report["report_id"],
        )

    payload = {
        "report_id": report["report_id"],
        "customer_email": customer_email.strip(),
        "approved": bool(approved),
        "vehicle_type": vehicle_type,
        "complaint": complaint.strip(),
        "observation": observation.strip(),
        "recommendation": recommendation.strip(),
        "transcript": transcript.strip(),
    }

    with st.expander("Preview JSON payload"):
        st.json(payload)
    st.download_button(
        "Download reviewed JSON",
        data=json.dumps(payload, indent=2, ensure_ascii=False),
        file_name=f"{report['report_id']}.json",
        mime="application/json",
    )

    if submit:
        if not approved:
            st.warning("Please review and approve the report first.")
        elif not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", customer_email.strip()):
            st.error("Enter a valid customer email address.")
        elif not all([complaint.strip(), observation.strip(), recommendation.strip()]):
            st.error("Complaint, observation, and recommendation are required.")
        else:
            try:
                with st.spinner("Submitting approved report to n8n..."):
                    response = submit_to_n8n(payload)
                st.session_state.submitted_id = report["report_id"]
                st.success("Report submitted to n8n successfully.")
                st.info(
                    "Submission accepted by the webhook. Verify the n8n execution and "
                    "autoservice_reports table to confirm the email was sent and status is SENT."
                )
                st.caption(f"Webhook HTTP status: {response.status_code}")
            except requests.exceptions.Timeout:
                st.error(
                    "The webhook timed out. Check n8n executions before retrying: "
                    "the report may already have been processed."
                )
            except requests.exceptions.RequestException as exc:
                st.error(f"Webhook request failed: {exc}")
            except ValueError as exc:
                st.error(str(exc))

    if st.session_state.submitted_id == report["report_id"]:
        st.success(f"Already submitted from this session: {report['report_id']}")
        if st.button("Start a new inspection"):
            st.session_state.report = None
            st.session_state.submitted_id = None
            st.rerun()
