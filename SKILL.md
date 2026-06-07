---
name: final-review
description: Use when user wants AI to help with final-exam review, cram sessions, question generation, knowledge-point tutoring, or exam-oriented note cleanup based on uploaded course materials. Prioritize past exam papers, teacher PPTs, homework, and structured Markdown study materials; convert raw files with markitdown; clean materials before generating questions, answers, and scoring-oriented explanations.
---

# Final Review

Exam-review skill for turning user materials into exam-oriented notes, questions, and explanations.

## Core Rules

- Work from user-provided materials first. Do not rely on random external question banks.
- Default to converting uploaded materials into Markdown with `markitdown`.
- Clean Markdown before extracting knowledge points or generating questions.
- Optimize for exam performance: what to memorize, what to write, how to score.

## Source Priority

When generating questions or judging what matters most, use this priority:

1. Past exam papers
2. Teacher PPTs
3. Homework / reading assignments
4. Crash-course question sets
5. AI supplemental questions

Crash-course materials have lower question-source priority, but high value for:

- identifying core knowledge points
- building chapter structure
- summarizing key ideas, common traps, and review order

## Material Workflow

### Step 1: Clarify Exam Shape

Before deep review work, first confirm as much of this as possible:

- what question types the exam uses
- which parts the teacher emphasized
- whether there are common proof / short-answer / objective-question patterns
- whether the user wants plain text, HTML, or both

If user already gave this, do not ask again.

### Step 2: Convert Everything to Markdown

Convert PPTs, PDFs, Word files, images, notes, and other study materials into Markdown.

Preferred tool:

```bash
markitdown
```

Principle:

- convert first
- clean second
- extract knowledge points third
- generate questions last

### Step 3: Clean Markdown

Cleaning is required. Review material is not archival storage.

Focus on:

- removing garbled text
- removing repeated content
- removing low-value filler
- merging overlapping definitions and conclusions
- keeping definitions, properties, theorems, standard methods, common exam patterns, and traps

Target:

- clear
- memorizable
- exam-usable

## Question Generation Rules

- Generate questions according to real exam question types, not generic balance.
- Each knowledge point should usually produce `1~6` questions.
- Denser, higher-frequency, more testable knowledge points get more questions.
- Thin or low-frequency knowledge points get fewer questions.
- Match question type to likely exam style.

Examples:

- objective / recognition-heavy topics -> choice, true-false, fill-in
- definition / comparison-heavy topics -> short answer
- reasoning / proof / construction-heavy topics -> subjective problems

## Lightweight Source Labels

Do not require file-level or page-level tracing by default.

Each generated question should still carry a lightweight category label when practical, such as:

- `题源：历年真题`
- `题源：老师PPT`
- `题源：平时作业`
- `题源：速成课框架改编`
- `题源：AI补充`

This is category-level guidance, not strict audit logging.

## Output Rules

### Plain Text Output

- Put questions first.
- Put answers and explanations at the end.
- Keep question area and answer area clearly separated.

### HTML Output

- Answers may appear under each question.
- Answers should be collapsed by default.
- User should expand to see answer and explanation.

## Explanation Rules

Every explanation should help user score better on the exam, not just understand content.

Try to include:

- correct answer / reference answer
- tested knowledge point
- common exam pattern
- how to score

### Objective Questions

For choice, true-false, fill-in, and similar objective questions, focus on:

- `这题核心知识点`
- key definition / property / decision rule
- easy confusion point

### Subjective Questions

For short answer, proof, analysis, computation, construction, and similar subjective questions, focus on:

- `这题答题核心点`
- `必须出现`
- `常见失分点`
- scoring-oriented answer structure

## Scoring-Oriented Guidance

Prefer actionable exam advice, not motivational filler.

Examples:

- see a true-false question -> check boundary conditions or counterexamples first
- see a short answer -> write definition, then property, then conclusion
- see a proof -> state knowns, target, and chosen theorem / definition entry point
- if full solution is unclear -> write core definition, key property, and conclusion first to grab partial credit

## AGENT.md / CLAUDE.md Sync

When this skill is used in a repo that has `AGENT.md` or `CLAUDE.md`, keep the short-form persistent rules aligned with this skill:

- use `markitdown`
- clean Markdown before question generation
- prioritize past papers, PPTs, homework, then crash-course materials
- separate questions and answers
- add `这题核心知识点` for objective questions
- add `这题答题核心点` / `必须出现` / `常见失分点` for subjective questions
- keep explanations exam- and scoring-oriented
