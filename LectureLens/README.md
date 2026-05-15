# 🎙️ LectureLens AI

LectureLens AI is a real-time AI system that converts live lecture audio into structured learning insights for students with ADHD and hearing impairments.

Instead of manually taking notes while trying to listen, students receive **automatically extracted Action Items and Key Concepts in real time**, allowing them to focus on understanding rather than transcription.

---

## 🚨 Problem

Students often struggle to balance listening and note-taking during lectures, resulting in:
- Missed deadlines and assignments
- Incomplete understanding of key concepts
- Cognitive overload during fast-paced lectures

LectureLens is designed to reduce this cognitive load by acting as a real-time AI note-taking layer.

---

## 🚀 Features

- 🎤 Browser-based lecture audio recording
- 📝 Whisper-powered speech-to-text transcription
- 🧠 GPT-4o-mini structured extraction:
  - Action Items (tasks, deadlines, assignments)
  - Key Concepts (definitions, frameworks, ideas)
- 📚 Streamlit-based real-time UI for insights visualization

---

## 🧠 System Design

LectureLens operates as a real-time AI pipeline:

1. Audio is captured from the browser microphone
2. Audio is transcribed using OpenAI Whisper
3. Transcript is sent to GPT-4o-mini
4. LLM extracts structured JSON output:
   - Action Items
   - Key Concepts
5. Streamlit renders structured insights in the UI

---

## 🏗️ Tech Stack

- Python
- Streamlit
- OpenAI Whisper API
- GPT-4o-mini
- audio-recorder-streamlit

---

## 📸 Demo

🔗 https://www.loom.com/share/35d680d3aff04be78001c16854e56ec6

---

## 🧪 Example Output

### 🔴 Action Items
- Submit Assignment 2 by Friday
- Prepare Chapter 3 notes

### 🔵 Key Concepts
- Gradient Descent
- Neural Networks
- Backpropagation

---

## 📦 Run Locally

```bash
git clone https://github.com/your-username/lecturelens-ai.git
cd lecturelens-ai
pip install -r requirements.txt
streamlit run app.py
