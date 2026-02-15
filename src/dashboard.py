import streamlit as st
import pandas as pd
import json
import os
import plotly.express as px
from typing import List, Dict

# Constants
PROCESSED_DATA_PATH = os.path.join("data", "processed", "jobs.csv")

def load_data() -> pd.DataFrame:
    """Loads processed job data from CSV."""
    if not os.path.exists(PROCESSED_DATA_PATH):
        # Fallback: Try to use st.secrets for PROCESSED_DATA_PATH if cloud hosted (advanced)
        # But primarily we just return empty
        return pd.DataFrame()
    return pd.read_csv(PROCESSED_DATA_PATH)

def render_metrics(df: pd.DataFrame):
    """Renders key metrics at the top of the dashboard."""
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Jobs", len(df))
    
    remote_jobs = df[df['remote_type'].astype(str).str.contains('GLOBAL|US_ONLY|EU_ONLY', case=False, na=False)]
    col2.metric("Remote Opportunities", len(remote_jobs))
    
    if 'salary_year_usd' in df.columns:
        avg_salary = df['salary_year_usd'].mean()
        col3.metric("Avg Salary (USD)", f"${avg_salary:,.0f}" if pd.notnull(avg_salary) else "N/A")

def render_tech_stack_chart(df: pd.DataFrame):
    """Renders a bar chart of the most popular technologies."""
    if 'tech_stack' not in df.columns:
        return

    # tech_stack is stored as a string representation of a list in CSV usually, 
    # e.g. "['Python', 'React']". Need to parse it.
    # Or cleaner: We'll explode it.
    
    # Helper to clean and split
    def parse_stack(stack_str):
        try:
            # stack_str is stored as a string representation of a list
            import ast
            return ast.literal_eval(stack_str)
        except:
            return []

    # Explode
    all_tech = []
    for stack_str in df['tech_stack'].dropna():
        try:
            import ast
            tech_list = ast.literal_eval(stack_str)
            if isinstance(tech_list, list):
                all_tech.extend(tech_list)
        except:
            pass
            
    if not all_tech:
        st.warning("No tech stack data found.")
        return

    tech_counts = pd.Series(all_tech).value_counts().head(20)
    
    fig = px.bar(
        x=tech_counts.index, 
        y=tech_counts.values,
        labels={'x': 'Technology', 'y': 'Job Count'},
        title="Top 20 Most Requested Technologies"
    )
    st.plotly_chart(fig, use_container_width=True)

def render_remote_policy_chart(df: pd.DataFrame):
    """Renders a pie chart of remote policies."""
    if 'remote_type' not in df.columns:
        return
        
    policy_counts = df['remote_type'].value_counts()
    
    fig = px.pie(
        names=policy_counts.index,
        values=policy_counts.values,
        title="Remote Work Availability"
    )
    st.plotly_chart(fig, use_container_width=True)

def main():
    st.set_page_config(page_title="Global Tech Intelligence Node", layout="wide")
    
    st.title("🌐 Global Tech Intelligence Node")
    st.markdown("Automated Market Intelligence from Hacker News 'Who is Hiring' threads.")
    
    df = load_data()
    
    # Preprocess for Links
    if not df.empty and 'hn_id' in df.columns:
        # Create actual clickable links for Streamlit LinkColumn
        # Streamlit LinkColumn expects a URL string.
        df['hn_id'] = "https://news.ycombinator.com/item?id=" + df['hn_id'].astype(str)

    # Preprocess Timestamp
    if not df.empty and 'timestamp' in df.columns:
        # Convert Unix timestamp to UTC datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
        # Format as readable string "YYYY-MM-DD HH:MM"
        df['timestamp'] = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M UTC')
    
    if df.empty:
        st.warning(f"No processed data found at `{PROCESSED_DATA_PATH}`. Please run the scraper and analyzer first.")
        return

    # --- DaaS Teaser Logic ---
    # total_jobs = len(df) # HIDDEN: We use static marketing numbers now
    avg_salary = df['salary_year_usd'].mean() if 'salary_year_usd' in df.columns else 0
    
    # Slice for Free Tier
    df_teaser = df.head(50).copy()
    
    # Mask Application URL in Teaser
    if 'application_url' in df_teaser.columns:
        df_teaser['application_url'] = "🔒 Upgrade to Unlock"
    
    st.warning(f"🔒 **Free Tier Preview:** Showing top 50 rows only. Hundreds of hidden premium jobs available in the full dataset.")

    # --- Sidebar Filters ---
    st.sidebar.header("🔓 Unlock Full Access")
    st.sidebar.markdown(f"""
    Get the complete **400+ job** dataset with:
    *   ✅ Direct Application Links
    *   ✅ Full Salary Data
    *   ✅ Daily Updates
    """)
    st.sidebar.link_button("👉 Download Full CSV ($7/mo)", "https://galacticgamer62.gumroad.com/l/mbkfwv")
    st.sidebar.divider()
    
    st.sidebar.header("Filters (Preview)")
    
    # 1. Tech Stack Filter
    all_tech_unique = set()
    for stack_str in df['tech_stack'].dropna():
        try:
            import ast
            t_list = ast.literal_eval(stack_str)
            if isinstance(t_list, list):
                all_tech_unique.update(t_list)
        except:
            pass
            
    selected_tech = st.sidebar.multiselect("Tech Stack", sorted(list(all_tech_unique)))
    
    # 2. Remote Policy Filter
    all_policies = df['remote_type'].dropna().unique().tolist()
    friendly_policies = ["All"] + sorted([p for p in all_policies if p])
    selected_policy = st.sidebar.selectbox("Remote Policy", friendly_policies)

    # 3. Visa Sponsorship Filter
    visa_options = ["All", "Yes", "No"]
    selected_visa = st.sidebar.selectbox("Visa Sponsorship Available", visa_options)

    # 4. Experience Level Filter
    if 'experience_level' in df.columns:
        all_exp = df['experience_level'].dropna().unique().tolist()
        selected_exp = st.sidebar.multiselect("Experience Level", sorted(all_exp))
    else:
        selected_exp = []

    # 5. Job Role Filter
    if 'job_role' in df.columns:
        all_roles = df['job_role'].dropna().unique().tolist()
        selected_role = st.sidebar.multiselect("Job Role", sorted(all_roles))
    else:
        selected_role = []

    # 6. Industry Filter
    if 'company_industry' in df.columns:
        all_inds = df['company_industry'].dropna().unique().tolist()
        selected_ind = st.sidebar.multiselect("Industry", sorted(all_inds))
    else:
        selected_ind = []
    
    # --- Apply Filters --
    filtered_df = df_teaser.copy()
    
    # Tech Stack
    if selected_tech:
        mask = filtered_df['tech_stack'].apply(lambda x: any(item in x for item in selected_tech) if isinstance(x, str) else False)
        filtered_df = filtered_df[mask]
        
    # Remote Policy
    if selected_policy != "All":
        filtered_df = filtered_df[filtered_df['remote_type'] == selected_policy]

    # Visa Sponsorship
    if selected_visa == "Yes":
        filtered_df = filtered_df[filtered_df['visa_sponsorship'].isin([True, "True", "true"])]
    elif selected_visa == "No":
        filtered_df = filtered_df[filtered_df['visa_sponsorship'].isin([False, "False", "false"])]

    # Experience Level
    if selected_exp:
        filtered_df = filtered_df[filtered_df['experience_level'].isin(selected_exp)]

    # Job Role
    if selected_role:
        filtered_df = filtered_df[filtered_df['job_role'].isin(selected_role)]

    # Industry
    if selected_ind:
        filtered_df = filtered_df[filtered_df['company_industry'].isin(selected_ind)]
    
    # --- Render Metrics & Charts ---
    # Use FULL DF for metrics to tease value
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Jobs (Global)", "400+") # STATIC
    col2.metric("Avg Salary (Global)", f"${avg_salary:,.0f}" if avg_salary else "N/A")
    col3.metric("Remote Jobs (Global)", len(df[df['remote_type'].astype(str).str.contains('GLOBAL|US_ONLY|EU_ONLY', case=False, na=False)]))
    
    col1, col2 = st.columns(2)
    with col1:
        render_tech_stack_chart(df) # Show full market trends
    with col2:
        render_remote_policy_chart(df)
        
    # --- Job Listings (Teaser Only) ---
    st.subheader(f"Free Preview ({len(filtered_df)} matches in top 50)")
    
    # Pagination Logic (Simplified for Teaser)
    # Just show the dataframe, no complex pagination needed for 50 rows.
    
    st.dataframe(
        filtered_df, 
        use_container_width=True,
        column_config={
            "hn_id": st.column_config.LinkColumn("HN Link", display_text="View on HN"),
            "application_url": st.column_config.TextColumn("Apply (Locked)", help="Upgrade to unlock direct links"), # Changed to TextColumn
            "salary_year_usd": st.column_config.NumberColumn("Salary (USD)", format="$%d"),
            "experience_level": "Experience",
            "job_role": "Role",
            "company_industry": "Industry",
            "timestamp": "Date Posted (UTC)"
        }
    )
    
if __name__ == "__main__":
    main()
