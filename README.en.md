# final-review

An exam-oriented review Skill. At its core, it is **a set of stable, reusable, score-driven review rules** rather than a chain of utility scripts.

It fits scenarios like these:

- You already have your own course materials
- You want an AI to clean your notes, extract knowledge points, and generate practice questions with explanations
- You want questions that mirror real exams, not random question-bank style
- You want the explanations to also cover "what to write to actually score points"

## What This Skill Does

`final-review` guides the Agent through the following workflow:

1. Confirm the exam question types, instructor's emphasis, and common test patterns
2. Convert uploaded materials to Markdown first, using `markitdown` whenever possible
3. Clean the Markdown, then extract knowledge points, then generate questions
4. Judge what is worth reviewing by source priority
5. Generate `1–6` questions per knowledge point, weighted by density and test likelihood
6. Separate questions from answers by default
7. For objective questions, append a "Core Knowledge Point" note
8. For subjective questions, append "Core Scoring Point", "Must-Include Elements", and "Common Pitfalls"
9. Make every explanation serve exam scoring

## Core Rules

### Question Source Priority

When generating questions and judging what to emphasize, follow this order strictly:

1. Past exam papers
2. Instructor's slides
3. Regular homework / reading assignments
4. Crash-course questions
5. AI-supplemented questions

Notes:

- Crash-course questions rank lower in priority
- However, crash courses are highly valuable for organizing knowledge points, building chapter structure, and summarizing key points

### Material Processing Rules

All materials should be converted to Markdown first. The recommended tool is:

```bash
markitdown
```

The unified workflow is:

1. Convert to Markdown
2. Clean the Markdown
3. Extract knowledge points
4. Generate questions last

Cleaning priorities:

- Remove garbled characters
- Remove duplicates
- Remove filler content
- Pull out core definitions, properties, conclusions, methods, and common pitfalls

The goal is not "complete archival" — it is "clear, memorable, and exam-ready".

### Question Generation Rules

- Generate questions by real exam format, not by mechanical distribution
- Typically `1–6` questions per knowledge point
- Denser, higher-frequency, and more testable knowledge points get more questions
- Question type must match the most likely exam style for the knowledge point

### Question Source Tagging Rules

Strict file-level or page-level traceability is not required by default.

However, keep lightweight category-level tags, for example:

- `Source: Past exam papers`
- `Source: Instructor slides`
- `Source: Regular homework`
- `Source: Crash-course framework adapted`
- `Source: AI supplemented`

### Output Rules

Plain-text output:

- Only questions appear at the top
- All answers and explanations are grouped at the end
- The question section and answer section must be clearly separated

HTML output:

- Answers are placed under the question
- Answers are collapsed by default
- They expand on click to reveal the full answer and explanation

### Explanation Rules

Explanations should serve scoring, not merely explain correctness.

For objective questions, emphasize:

- `Core knowledge point of this question`
- Key definitions, properties, and judgment criteria
- Commonly confused points

For subjective questions, emphasize:

- `Core scoring point of this question`
- `Must-include elements`
- `Common pitfall`
- A standard answer structure

Whenever possible, explanations should also cover:

- Which words must appear in the answer
- What to write first to grab step-by-step points
- Which mistakes most easily cost you marks

## Repository Structure

This repository is centered on a **rule-based Skill**:

- [`SKILL.md`](./SKILL.md) — the full workflow and rules
- [`AGENT.md`](./AGENT.md) — a long-form default ruleset you can copy directly into an Agent / Claude configuration

## How to Use

You can treat this Skill as:

1. A review protocol to hand directly to an Agent
2. A template for your own course review workflow
3. A foundation Skill that can be extended for different subjects

If your Agent supports long-term rule files, you can sync the rules from [`AGENT.md`](./AGENT.md) directly into your own `AGENT.md` or `CLAUDE.md`.

In addition, one of the design goals of this skill is to **automatically check and sync such long-term rule files** after running, so that:

- The complete workflow in `SKILL.md`
- The long-term default rules in `AGENT.md` / `CLAUDE.md`

stay in sync and do not drift into two conflicting logics.

## Subject Extensions

Contributions of subject-specific content are highly welcome, for example:

- Calculus
- Discrete Mathematics
- Data Structures
- Computer Networks
- Operating Systems

If you have unique study techniques, question-type heuristics, answer templates, or already-cleaned Markdown notes for a specific subject, feel free to extend this repository on top of it.

Suggested directions:

- Subject-specific question type summaries
- High-frequency test points
- Common pitfalls
- Answer templates for proof, computation, and short-answer questions
- Subject-specific Markdown material
- Subject-specific Skill rules

When developing, please organize your work in a feature branch and submit a Pull Request to merge it back, rather than scattering content across long-lived branches.

## Contributing

Contributions that improve this Skill from real review experience are welcome. Especially valuable:

- Better question source priority strategies
- More robust Markdown cleaning rules
- Question generation protocols that match real exams more closely
- Stronger objective / subjective question explanation templates
- More practical exam-scoring technique summaries
- Subject-specific review rules and materials for a concrete course

Recommended contribution flow:

1. Fork this repository
2. Create a new branch from your fork
3. Modify `SKILL.md`, `AGENT.md`, `README.md`, or add subject-specific material on the branch
4. Commit your changes
5. Push to your fork
6. Open a Pull Request to the main repository
7. In the PR, describe:

   - What you changed
   - Why you changed it
   - Which review scenario it applies to

If your contribution is tailored to a personal course, please mark the applicable scope in the PR so reviewers can decide whether to merge it into the main branch.
