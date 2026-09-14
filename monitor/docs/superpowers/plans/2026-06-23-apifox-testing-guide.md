# Apifox Seven-API Testing Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a copy-ready Markdown guide for testing all seven Virgo APIs in Apifox.

**Architecture:** The guide uses one Apifox environment, response extraction scripts, and a strict end-to-end execution order. Every request includes exact headers/body, expected response and failure diagnostics; SSE has an Apifox flow plus curl fallback.

**Tech Stack:** Markdown, Apifox/Postman-compatible `pm` scripts, PowerShell, curl, PostgreSQL SQL

---

1. Create `docs/apifox-seven-api-testing-guide.md` with prerequisites, environment initialization and Token boundaries.
2. Add seven exact request recipes, response extraction scripts and assertions in executable order.
3. Add idempotency/error cases, PostgreSQL verification queries and troubleshooting.
4. Scan the final guide for placeholders, inconsistent variable names, wrong routes and missing required headers.
