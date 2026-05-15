# 🎙️ LectureLens AI

LectureLens AI is an AI-assisted lecture companion designed to help students with ADHD and hearing impairments by converting raw lecture audio into structured learning insights in real time.

It transcribes lectures and extracts **Action Items** and **Key Concepts**, helping students focus on understanding instead of manual note-taking.

---

## 🚀 Features

- 🎤 Record lecture audio directly in the browser
- 📝 Whisper-based speech-to-text transcription
- 🧠 GPT-4o-mini extracts structured insights:
  - Action Items (assignments, deadlines)
  - Key Concepts (definitions, frameworks)
- 📚 Streamlit UI for clean visualization of insights

---

## 🧠 How It Works

1. User records lecture audio
2. Audio is sent to OpenAI Whisper for transcription
3. Transcript is passed to GPT-4o-mini
4. Model extracts structured JSON output:
   - Action Items
   - Key Concepts
5. Streamlit displays results in UI

---

## 🏗️ Tech Stack

- Python
- Streamlit
- OpenAI Whisper API
- GPT-4o-mini
- audio-recorder-streamlit

---

## 📸 Demo

(Add GIF or screenshot here)

---

## 🧪 Example Output

**Action Items**
- Submit Assignment 2 by Friday
- Prepare Chapter 3 notes

**Key Concepts**
- Gradient Descent
- Neural Networks
- Backpropagation

---

## 📦 Installation

```bash
git clone https://github.com/your-username/lecturelens-ai.git
cd lecturelens-ai
pip install -r requirements.txt
streamlit run app.py
