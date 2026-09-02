import streamlit as st
from google.cloud import storage
from google.cloud import firestore
import pandas as pd
import time

# --- CONFIGURATION (UPDATE THESE!) ---
PROJECT_ID = "gcp-new-project-506608"
BUCKET_NAME = "talent-ai-summarizer"

# Initialize GCP Clients
@st.cache_resource
def get_gcp_clients():
    return storage.Client(project=PROJECT_ID), firestore.Client(project=PROJECT_ID, database="jddatabase")

storage_client, db = get_gcp_clients()

st.set_page_config(page_title="TalentAI HR Dashboard", page_icon="💼", layout="wide")
st.title("TalentAI: Automated Resume Screener")

# Helper function to fetch all JDs and distinct teams
def get_all_job_descriptions():
    jds_ref = db.collection("job_descriptions").stream()
    jd_list = []
    teams = set()
    for jd in jds_ref:
        data = jd.to_dict()
        team = data.get("team", "Unassigned")
        if team:
            teams.add(team)
        jd_list.append({
            "doc_id": jd.id,
            "Job ID": data.get("job_id", "N/A"),
            "Team": team,
            "Job Title": data.get("title", "N/A"),
            "Experience (Years)": data.get("experience_years", 0),
            "Requirements & Description": data.get("requirements", "")
        })
    return jd_list, sorted(list(teams))

# Fetch initial data for dynamic dropdowns
all_jds, available_teams = get_all_job_descriptions()

# Create Tabs for the UI
tab1, tab2, tab3 = st.tabs(["📄 Upload Resume", "📋 Manage Job Descriptions", "📊 Candidate Results"])

# --- TAB 1: UPLOAD RESUME ---
with tab1:
    st.header("Upload a Candidate Resume")
    uploaded_file = st.file_uploader("Choose a file (.txt or .pdf)", type=['txt', 'pdf'], accept_multiple_files=False)
    
    # Dynamic multiselect populated from unique teams in JD collection
    selected_teams = st.multiselect(
        "Target Teams for Evaluation",
        options=available_teams,
        placeholder="Select one or more teams to evaluate against (optional)...",
        help="Leave empty to evaluate against all open positions."
    )
    
    if st.button("Submit Resume", type="primary") and uploaded_file is not None:
        with st.spinner("Uploading to Cloud Storage... (This will trigger the AI pipeline)"):
            bucket = storage_client.bucket(BUCKET_NAME)
            blob = bucket.blob(uploaded_file.name)
            
            # Pass selected teams in metadata so Cloud Functions/Eventarc can filter evaluations
            blob.metadata = {
                "target_teams": ",".join(selected_teams) if selected_teams else "ALL"
            }
            
            blob.upload_from_file(uploaded_file, content_type=uploaded_file.type)
            blob.patch()
            
        st.success(f"Successfully uploaded **{uploaded_file.name}**! The AI is evaluating it for: {', '.join(selected_teams) if selected_teams else 'All Teams'}.")
        st.info("Wait about 10-15 seconds, then check the 'Candidate Results' tab.")

# --- TAB 2: MANAGE JOB DESCRIPTIONS ---
with tab2:
    st.header("Add a New Open Position")
    with st.form("jd_form", clear_on_submit=True):
        col1, col2, col3 = st.columns([0.25, 0.45, 0.3])
        with col1:
            job_id = st.text_input("Job ID", placeholder="e.g., DS-101")
        with col2:
            job_title = st.text_input("Job Title", placeholder="e.g., Senior Data Scientist")
        with col3:
            team_name = st.text_input("Team / Department", placeholder="e.g., Data Science, CloudOps")
            
        col4, _ = st.columns([0.3, 0.7])
        with col4:
            experience_years = st.number_input("Min Experience (Years)", min_value=0, max_value=30, value=2, step=1)

        job_reqs = st.text_area(
            "Requirements & Description",
            placeholder="e.g., We are looking for a Data Scientist to design, develop, and deploy machine learning solutions..."
        )
        
        submitted = st.form_submit_button("Save Job Description", type="primary")
        if submitted:
            if job_id and job_title and team_name and job_reqs:
                # Check for Job ID uniqueness
                existing = db.collection("job_descriptions").where("job_id", "==", job_id.strip()).limit(1).get()
                if len(existing) > 0:
                    st.error(f"Job ID '{job_id}' already exists. Please provide a unique Job ID.")
                else:
                    db.collection("job_descriptions").add({
                        "job_id": job_id.strip(),
                        "team": team_name.strip(),
                        "title": job_title.strip(),
                        "experience_years": experience_years,
                        "requirements": job_reqs.strip()
                    })
                    st.success(f"Successfully saved **[{job_id}] {job_title}** to the database!")
                    st.rerun()
            else:
                st.error("Please fill in Job ID, Job Title, Team, and Requirements fields.")

    st.divider()

    # Expandable container for Current Open Roles & Deletions
    with st.expander("📂 Current Open Roles & Management", expanded=True):
        if all_jds:
            df_jds = pd.DataFrame(all_jds).drop(columns=["doc_id"])
            
            search_query = st.text_input("🔍 Search / Filter Roles", placeholder="Type to filter by Job ID, Team, Title, or Description...")
            if search_query:
                filter_mask = (
                    df_jds["Job ID"].str.contains(search_query, case=False, na=False) |
                    df_jds["Team"].str.contains(search_query, case=False, na=False) |
                    df_jds["Job Title"].str.contains(search_query, case=False, na=False) |
                    df_jds["Requirements & Description"].str.contains(search_query, case=False, na=False)
                )
                df_jds = df_jds[filter_mask]

            st.dataframe(
                df_jds,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Job ID": st.column_config.TextColumn("Job ID", width="small"),
                    "Team": st.column_config.TextColumn("Team", width="medium"),
                    "Job Title": st.column_config.TextColumn("Job Title", width="medium"),
                    "Experience (Years)": st.column_config.NumberColumn("Min Exp", width="small"),
                    "Requirements & Description": st.column_config.TextColumn(width="large")
                }
            )
            
            # --- Remove / Invalidate JD Section ---
            st.markdown("####  Remove / Deactivate a Job Description")
            col_del_select, col_del_btn = st.columns([0.7, 0.3])
            
            with col_del_select:
                jd_options = {f"[{jd['Job ID']}] {jd['Job Title']} ({jd['Team']})": jd["doc_id"] for jd in all_jds}
                selected_jd_label = st.selectbox("Select JD to Remove", options=list(jd_options.keys()))
                
            with col_del_btn:
                st.write("")
                st.write("")
                if st.button("Delete Selected JD", type="secondary"):
                    doc_id_to_delete = jd_options[selected_jd_label]
                    db.collection("job_descriptions").document(doc_id_to_delete).delete()
                    st.success(f"Removed '{selected_jd_label}' from the database.")
                    time.sleep(1)
                    st.rerun()
        else:
            st.info("No open roles found in the database. Add one using the form above!")

# --- TAB 3: CANDIDATE SCORECARD DASHBOARD ---
with tab3:
    st.header("Candidate Evaluation Scorecard")
    try:
        bucket = storage_client.bucket(BUCKET_NAME)
        blobs = list(bucket.list_blobs())
        resume_files = [
            b.name for b in blobs
            if b.name.lower().endswith(('.pdf', '.txt'))
        ]
    except Exception as e:
        st.error(f"Error connecting to Cloud Storage: {e}")
        resume_files = []

    if resume_files:
        col_select, col_refresh = st.columns([0.8, 0.2])
        with col_select:
            selected_file = st.selectbox(
                "Select Candidate Resume from Cloud Storage",
                options=resume_files,
                index=0
            )
        with col_refresh:
            st.write("")
            st.write("")
            if st.button("🔄 Refresh", use_container_width=True):
                st.rerun()

        st.divider()
        
        eval_query = db.collection("candidate_evaluations").where("file_name", "==", selected_file).limit(1).stream()
        eval_docs = list(eval_query)
        if eval_docs:
            data = eval_docs[0].to_dict()
            candidate_name = data.get("candidate_name", "Unknown Candidate")
            evaluations = data.get("evaluations", [])

            col_title, col_retrigger = st.columns([0.7, 0.3])
            with col_title:
                st.subheader(f"Candidate: {candidate_name}")
            with col_retrigger:
                if st.button("Re-evaluate with All JDs", type="primary"):
                    with st.spinner("Triggering AI re-evaluation..."):
                        bucket = storage_client.bucket(BUCKET_NAME)
                        blob = bucket.blob(selected_file)
                        content = blob.download_as_bytes()
                        blob.upload_from_string(content, content_type=blob.content_type)
                        st.success("Re-evaluation triggered! Wait 10 seconds and click Refresh.")

            st.markdown("### Top Matches")
            if evaluations:
                top_roles = evaluations[:3]
                cols = st.columns(len(top_roles))
                for idx, eval_item in enumerate(top_roles):
                    score = eval_item.get("match_score", 0)
                    role = eval_item.get("job_title", "N/A")
                    with cols[idx]:
                        st.metric(
                            label=f"Rank #{idx+1}: {role}",
                            value=f"{score}%"
                        )
                st.divider()

                st.markdown("### Detailed AI Evaluation & Reasoning")
                df_scorecard = pd.DataFrame(evaluations)[["job_title", "match_score", "reason"]]
                st.dataframe(
                    df_scorecard,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "job_title": st.column_config.TextColumn("Matched Role", width="medium"),
                        "match_score": st.column_config.NumberColumn("Match Score (%)", format="%d%%", width="small"),
                        "reason": st.column_config.TextColumn("AI Reasoning", width="large")
                    }
                )
            else:
                st.warning("No role match evaluation details found for this candidate.")
        else:
            st.info("**Evaluation Pending...** The AI pipeline is either processing this resume or it has not been triggered yet. Click **Refresh** in a few seconds.")
    else:
        st.info("No PDF or TXT resume files found in Cloud Storage. Upload a resume in Tab 1 to get started!")