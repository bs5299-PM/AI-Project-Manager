# LectureLens AI — Real-Time AI Lecture Intelligence System

AI-powered real-time lecture assistant that converts live speech into structured Key Concepts and Action Items using Whisper + LLM-based extraction with HITL feedback loops.

## Problem Statement

Students with ADHD and hearing impairments struggle to simultaneously listen and take structured notes during live lectures. Existing transcription tools provide raw text but do not distinguish between actionable tasks and conceptual knowledge.

This leads to:
- Missed assignments
- Cognitive overload
- Poor retention of key concepts

## Solution Overview

LectureLens AI introduces a real-time intelligence layer over live lectures that:
- Transcribes speech using Whisper
- Extracts Action Items and Key Concepts using LLM agents
- Displays structured insights in a live sidebar
- Continuously improves via HITL feedback loops

## System Architecture

Pipeline:
Audio Capture → Chunking → Whisper STT → Context Buffer → LLM Extraction → Sidebar UI → Session Storage

Key Components:
- Speech-to-Text: Whisper (streaming)
- LLM Layer: Structured JSON extraction (Action Items / Key Concepts)
- Context Window: Rolling 60-second buffer
- Output: Real-time sidebar updates via WebSocket



## Key Design Decisions

- 3-second latency budget enforced across full pipeline
- Confidence-based filtering (threshold = 0.65)
- HITL feedback loop for continuous improvement
- Semantic deduplication to prevent sidebar noise
- Streaming-first architecture (no batch processing allowed)

## Acceptance Criteria

- End-to-end latency ≤ 3 seconds (P95)
- Action Item recall ≥ 90%
- Key Concept precision ≥ 85%
- System uptime ≥ 99.5%
- Zero CRITICAL hallucination failures in evaluation set

## Key Risks

- Hallucination of non-existent Action Items (CRITICAL)
- Missed deadline extraction from speech
- Latency spikes under poor network conditions
- Whisper transcription degradation in noisy environments

Mitigation:
- Confidence gating
- HITL validation loop
- Fallback transcription mode

## Role

AI Project Manager responsible for:
- Translating AI system constraints into execution plans
- Defining latency SLAs and accuracy thresholds
- Designing HITL feedback loops
- Structuring evaluation framework (RAGAS-style metrics)
- Coordinating ML + Engineering delivery alignment

## Evaluation Metrics

- Word Error Rate (STT): ≤10%
- Precision (Key Concepts): ≥85%
- Recall (Action Items): ≥90%
- F1 Score: ≥87%
- Latency P95: ≤3s

## Demo

Live system demonstrates:
- Real-time speech transcription
- Live sidebar extraction
- Action Item highlighting
- Key Concept summarization


## Tech Stack

- Whisper (STT)
- GPT-4 / Claude (LLM extraction)
- WebSocket (real-time delivery)
- PostgreSQL (session storage)
- React (UI layer)
