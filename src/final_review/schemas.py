from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")]
Text = Annotated[str, Field(min_length=1, max_length=20000)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceType(StrEnum):
    past_exam = "past_exam"
    teacher_ppt = "teacher_ppt"
    homework = "homework"
    crash_course = "crash_course"
    ai_supplement = "ai_supplement"


QuestionType = Literal["choice", "fill_blank", "true_false", "short_answer", "calculation", "proof"]


class MaterialInput(Model):
    course_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    source_type: SourceType
    chapter: Annotated[str, Field(max_length=200)] = ""
    markdown: Annotated[str, Field(min_length=1, max_length=500000)]


class ExamProfile(Model):
    question_types: list[QuestionType] = Field(min_length=1, max_length=6)
    emphasis: list[str] = Field(default_factory=list, max_length=30)
    excluded_topics: list[str] = Field(default_factory=list, max_length=30)


class AgentRequest(Model):
    course_id: Identifier
    session_id: Identifier
    message: Annotated[str, Field(min_length=1, max_length=4000)]
    intent: Literal["auto", "ask", "quiz"] = "auto"
    chapter: Annotated[str, Field(max_length=200)] = ""
    exam_profile: ExamProfile | None = None


class ChatMessage(Model):
    role: Literal["user", "assistant"]
    content: Annotated[str, Field(min_length=1, max_length=4000)]


class ChatRequest(Model):
    message: Text
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class ChatResponse(Model):
    reply: Text
    model: str


class ResumeRequest(Model):
    course_id: Identifier
    session_id: Identifier
    exam_profile: ExamProfile


class Submission(Model):
    course_id: Identifier
    session_id: Identifier
    answers: dict[str, Text] = Field(min_length=1, max_length=36)


class Evidence(Model):
    chunk_id: str
    document_id: str
    title: str
    course_id: str
    chapter: str
    source_type: SourceType
    content: str
    similarity: float
    rank_score: float = 0


class Citation(Model):
    chunk_id: str
    quote: Annotated[str, Field(min_length=1, max_length=1500)]


class GroundedAnswer(Model):
    answer: Text
    citations: list[Citation] = Field(min_length=1, max_length=10)


class KnowledgePoint(Model):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    summary: Text
    importance: Literal[1, 2, 3, 4, 5]
    question_count: int = Field(ge=1, le=6)
    question_types: list[QuestionType] = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)


class KnowledgePlan(Model):
    points: list[KnowledgePoint] = Field(min_length=1, max_length=6)


class Question(Model):
    id: str
    knowledge_point: str
    question_type: QuestionType
    stem: Text
    options: list[str] = Field(default_factory=list, max_length=8)
    reference_answer: Text
    core_point: Text
    must_include: list[str] = Field(min_length=1)
    common_mistakes: list[str] = Field(min_length=1)
    scoring_tips: Text
    source_type: SourceType
    citations: list[Citation] = Field(min_length=1)


class Quiz(Model):
    questions: list[Question] = Field(min_length=1, max_length=36)


class Grade(Model):
    question_id: str
    score: float = Field(ge=0, le=100)
    error_type: str
    feedback: Text
    missing_points: list[str]


class Grades(Model):
    items: list[Grade] = Field(min_length=1, max_length=36)


class Verification(Model):
    supported: bool
    issues: list[str]


class Route(Model):
    intent: Literal["ask", "quiz"]


class AgentResponse(Model):
    session_id: str
    status: Literal["completed", "needs_input", "awaiting_answers", "insufficient_evidence"]
    answer: str = ""
    questions: list[dict] = Field(default_factory=list)
    citations: list[Evidence] = Field(default_factory=list)
    assessment: dict | None = None
    weak_points: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    prompt: dict | None = None

    @model_validator(mode="after")
    def public_questions(self):
        private = {"reference_answer", "must_include", "scoring_tips", "common_mistakes"}
        if any(private & q.keys() for q in self.questions):
            raise ValueError("待作答响应不能包含参考答案")
        return self
