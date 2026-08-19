import functions_framework
from google.cloud import storage
from google.cloud import firestore
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig, Part
import os
import json
import datetime

# --- CONFIGURATION ---
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "project-f11574f8-4328-4d46-901")
REGION = "asia-south1"  # Vertex AI region
DATABASE_NAME = "jddatabase"  # Your custom Firestore database name

# Initialize clients globally for performance (warm starts)
storage_client = storage.Client()
db = firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)


@functions_framework.cloud_event
def evaluate_resume(cloud_event):
    data = cloud_event.data
    bucket_name = data["bucket"]
    file_name = data["name"]
    
    print(f"Triggered! Processing file: {file_name}")

    # 1. Fetch the blob & reload to access user custom metadata
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_name)
    blob.reload()  # Required to populate blob.metadata

    metadata = blob.metadata or {}
    target_teams_raw = metadata.get("target_teams", "ALL")
    
    # Parse target teams into a list of strings
    if target_teams_raw and target_teams_raw != "ALL":
        target_teams = [t.strip() for t in target_teams_raw.split(",") if t.strip()]
    else:
        target_teams = []

    print(f"Candidate target teams: {target_teams if target_teams else 'ALL'}")

    # 2. Fetch Job Descriptions from Firestore database & filter by Team
    jd_collection = db.collection("job_descriptions").stream()
    all_jobs = [{"id": jd.id, **jd.to_dict()} for jd in jd_collection]
    
    if target_teams:
        available_jobs = [
            job for job in all_jobs 
            if job.get("team", "").strip() in target_teams
        ]
    else:
        available_jobs = all_jobs

    if not available_jobs:
        print(f"No job descriptions matched target teams: {target_teams}. Exiting.")
        # Store an empty evaluation record to notify UI
        db.collection("candidate_evaluations").document(file_name).set({
            "file_name": file_name,
            "candidate_name": "N/A",
            "target_teams": target_teams if target_teams else ["ALL"],
            "evaluations": [],
            "processed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": "No matching open jobs found for the selected team(s)"
        })
        return

    # 3. Download the uploaded file from Cloud Storage & prepare Part/Text
    file_extension = file_name.split(".")[-1].lower()
    
    if file_extension == "pdf":
        pdf_bytes = blob.download_as_bytes()
        document_part = Part.from_data(data=pdf_bytes, mime_type="application/pdf")
    else:
        text_content = blob.download_as_text()
        document_part = f"RESUME CONTENT:\n{text_content}"

    # 4. Initialize Vertex AI and prepare prompt
    vertexai.init(project=PROJECT_ID, location=REGION)
    model = GenerativeModel("gemini-2.5-flash")
    
    prompt = f"""
    You are an expert IT Recruiter. Evaluate the attached candidate's resume ONLY against the provided Job Descriptions.
    
    OPEN JOB DESCRIPTIONS:
    {json.dumps(available_jobs, indent=2)}
    
    TASK:
    1. Extract the candidate's full name.
    2. Compare the candidate's qualifications against the filtered open positions.
    3. Rank the best-matching roles (up to top 3) for this candidate.
    4. Provide a Match Score (0-100) and a 1-sentence explanation for each match.
    
    Return EXACTLY this JSON structure:
    {{
        "candidate_name": "Extracted Full Name",
        "evaluations": [
            {{"job_title": "Role Title", "match_score": 85, "reason": "Reasoning here..."}}
        ]
    }}
    """
    
    # Force strict JSON formatting in model output
    generation_config = GenerationConfig(response_mime_type="application/json")
    
    response = model.generate_content(
        [document_part, prompt],
        generation_config=generation_config
    )
    
    result = json.loads(response.text)
    
    # 5. Enrich result payload with system & team metadata and store in Firestore
    result["file_name"] = file_name
    result["target_teams"] = target_teams if target_teams else ["ALL"]
    result["processed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result["status"] = "COMPLETED"
    
    db.collection("candidate_evaluations").document(file_name).set(result)
    print(f"Successfully evaluated '{result.get('candidate_name')}' for teams {target_teams or 'ALL'} and saved to Firestore.")