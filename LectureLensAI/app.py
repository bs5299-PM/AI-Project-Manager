from openai import OpenAI
import json
import requests
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_classic.chains import RetrievalQA
from langchain_classic.prompts import PromptTemplate
import streamlit as st

import tempfile
import os

client = OpenAI(api_key="")

import streamlit as st
from audio_recorder_streamlit import audio_recorder

st.title("🎙️ LectureLens AI — Real-Time Lecture Assistant")
st.caption("Built with OpenAI Whisper (STT) + GPT-4o-mini | Bindu Singh, PMP, CSM")
 
# ── Fix 5: Latency messaging ──
st.info("⏱️ Expected AI extraction latency: ~2–4 seconds")
 
st.divider()
 
# ── Session state ──
if "transcript" not in st.session_state:
    st.session_state.transcript = ""
if "action_items" not in st.session_state:
    st.session_state.action_items = []
if "key_concepts" not in st.session_state:
    st.session_state.key_concepts = []
 
# ── Step 1: Record audio ──
st.subheader("Step 1 — Record Your Lecture")
audio_bytes = audio_recorder(
    text="Click to record",
    recording_color="#e74c3c",
    neutral_color="#2ecc71",
    icon_size="2x"
)
 
# ── Fix 3: Text fallback ──
st.divider()
st.subheader("Or paste lecture text directly")
sample_text = st.text_area(
    "Paste lecture notes here (useful if mic is unavailable)",
    placeholder="Example: Assignment 2 is due next Friday. RAG stands for Retrieval Augmented Generation..."
)
 
# ── Step 2: Transcribe ──
if audio_bytes:
    st.success("✅ Audio recorded!")
 
    if st.button("📝 Transcribe Audio"):
        with st.spinner("Whisper is converting your audio to text..."):
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_bytes)
                temp_path = f.name
 
            with open(temp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )
            os.unlink(temp_path)
        st.session_state.transcript = transcript
 
# Use pasted text if no audio
if sample_text and not st.session_state.transcript:
    st.session_state.transcript = sample_text
 
# ── Show transcript ──
if st.session_state.transcript:
    st.divider()
    st.subheader("📄 Transcript")
    st.write(st.session_state.transcript)
 
    # ── Step 3: Extract ──
    if st.button("🧠 Extract Action Items & Key Concepts"):
        with st.spinner("GPT-4o-mini is analyzing... (~2-4 seconds)"):
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """You are a lecture assistant for students with ADHD.
Read the transcript and extract:
1. ACTION ITEMS: tasks, assignments, deadlines the student needs to do
2. KEY CONCEPTS: important ideas, definitions, frameworks to know
 
Return ONLY this JSON format, nothing else:
{
  "action_items": ["item 1", "item 2"],
  "key_concepts": ["concept 1", "concept 2"]
}
If nothing found, return empty lists."""
                    },
                    {
                        "role": "user",
                        "content": st.session_state.transcript
                    }
                ],
                temperature=0
            )
 
        result_text = response.choices[0].message.content.strip()
 
        # ── Fix 2: Error handling for JSON ──
        try:
            result_text = result_text.replace("```json", "").replace("```", "").strip()
            result = json.loads(result_text)
            st.session_state.action_items = result.get("action_items", [])
            st.session_state.key_concepts = result.get("key_concepts", [])
        except:
            st.error("⚠️ Failed to parse AI response. Please try again.")
 
# ── Show results ──
if st.session_state.action_items or st.session_state.key_concepts:
    st.divider()
    st.subheader("📋 Extracted Insights")
 
    col1, col2 = st.columns(2)
 
    with col1:
        st.markdown("### 🔴 Action Items")
        if st.session_state.action_items:
            for item in st.session_state.action_items:
                st.error(f"• {item}")
        else:
            st.info("No action items found")
 
    with col2:
        st.markdown("### 🔵 Key Concepts")
        if st.session_state.key_concepts:
            for item in st.session_state.key_concepts:
                st.info(f"• {item}")
        else:
            st.info("No key concepts found")
 
    # ── Download notes ──
    st.divider()
    export_text = "# LectureLens AI — Session Notes\n\n"
    export_text += "## 🔴 Action Items\n"
    for item in st.session_state.action_items:
        export_text += f"- {item}\n"
    export_text += "\n## 🔵 Key Concepts\n"
    for item in st.session_state.key_concepts:
        export_text += f"- {item}\n"
    export_text += f"\n## 📄 Full Transcript\n{st.session_state.transcript}"
 
    st.download_button(
        label="⬇️ Download Notes as Markdown",
        data=export_text,
        file_name="lecturelens_notes.md",
        mime="text/markdown"
    )
 
    # ── Reset ──
    if st.button("🔄 Start New Session"):
        st.session_state.transcript = ""
        st.session_state.action_items = []
        st.session_state.key_concepts = []
        st.rerun()
 
# ── Sidebar ──
with st.sidebar:
    st.markdown("## 🎙️ LectureLens AI")
    st.markdown("**How it works:**")
    st.markdown("""
1. 🎙️ Record your lecture
2. 📝 Whisper transcribes audio → text
3. 🧠 GPT-4o-mini extracts insights
4. 📋 Review Action Items & Key Concepts
5. ⬇️ Download your notes
""")
    st.divider()
    st.markdown("**Built with:**")
    st.markdown("- OpenAI Whisper (Speech-to-Text)")
    st.markdown("- GPT-4o-mini (AI extraction)")
    st.markdown("- Streamlit (UI)")
    st.divider()
    st.markdown("**PRD:** LectureLens AI v1.0")
    st.markdown("**Author:** Bindu Singh, PMP, CSM")