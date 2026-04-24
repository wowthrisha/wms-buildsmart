from __future__ import annotations

from app.models import Requirement


ARCHITECT_ASSISTANT_PROMPTS = [
    'What approval risks should I flag on this project right now?',
    'Summarize the likely TNPCR constraints I should validate before submission.',
    'Which compliance documents usually become architect-side blockers?',
    'Help me explain the biggest rule risk to the client in clean language.',
]

CLIENT_ASSISTANT_PROMPTS = [
    'Explain FAR and site coverage in simple words.',
    'What documents usually delay approval the most?',
    'What setbacks should we watch for on this project?',
    'What should I ask my architect before the next design review?',
]


def _requirements_summary(requirements: list[Requirement]) -> dict:
    return {
        'total': len(requirements),
        'needs_review': sum(1 for requirement in requirements if requirement.status == 'new'),
        'client_requests': sum(1 for requirement in requirements if requirement.source == 'client'),
        'completed': sum(1 for requirement in requirements if requirement.status == 'done'),
    }


def _client_prompts(profession: str | None) -> list[str]:
    prompts = list(CLIENT_ASSISTANT_PROMPTS)
    profession_value = (profession or '').strip()
    if profession_value:
        prompts.append(f'Explain approval risk in a {profession_value.lower()} analogy.')
    return prompts


def _workspace_profile(user, project, summary: dict) -> dict:
    profession = ((getattr(user, 'profession', None) or '').strip() if user.role == 'client' else '')
    if user.role == 'architect':
        return {
            'assistant_audience': 'architect',
            'assistant_kicker': 'Architect Regulations Assistant',
            'assistant_title': 'Ask BuildSmart',
            'assistant_subtitle': 'A technical workspace for rule interpretation, approval strategy, compliance risk spotting, and architect-grade project guidance.',
            'assistant_badge': 'Technical Mode',
            'assistant_intro_message': 'I can help you interpret rules, spot submission risk, and frame precise client guidance while staying grounded in the BuildSmart handbook bundle.',
            'assistant_context_copy': 'Ask for technical interpretations, compliance tradeoffs, document blockers, or how to communicate risks clearly to the client.',
            'assistant_input_placeholder': 'Ask about TNPCR limits, approval strategy, or compliance risk',
            'assistant_lens_label': 'Response Style',
            'assistant_lens_value': 'Architect-focused',
            'assistant_lens_copy': 'Responses stay concise, technical, and action-oriented so you can use them in design or approval discussions.',
            'assistant_prompts': ARCHITECT_ASSISTANT_PROMPTS,
            'assistant_profession': '',
            'assistant_profession_hint': '',
            'assistant_endpoint': f'/projects/{project.id}/assistant/query',
        }

    profession_title = profession.title() if profession else 'Client-friendly'
    hint = (
        f'BuildSmart will explain rules with polished analogies that make sense to a {profession_title}.'
        if profession
        else 'Add your profession in Settings if you want responses framed with profession-aware analogies.'
    )
    return {
        'assistant_audience': 'client',
        'assistant_kicker': 'Client Regulations Assistant',
        'assistant_title': 'Ask BuildSmart',
        'assistant_subtitle': 'A plain-language workspace for understanding plot rules, approval readiness, and architect guidance without getting lost in technical jargon.',
        'assistant_badge': 'Profession-aware' if profession else 'Plain Language',
        'assistant_intro_message': 'I can explain technical rules in clear everyday language. When your profession is saved, I also frame tougher ideas with professional analogies that still sound polished.',
        'assistant_context_copy': 'Ask in simple words. I will keep the answer practical, client-friendly, and aligned to your project stage and compliance context.',
        'assistant_input_placeholder': 'Ask about approvals, setbacks, FAR, documents, or your project risks',
        'assistant_lens_label': 'Communication Lens',
        'assistant_lens_value': profession_title,
        'assistant_lens_copy': hint,
        'assistant_prompts': _client_prompts(profession),
        'assistant_profession': profession,
        'assistant_profession_hint': hint,
        'assistant_endpoint': '/my_project/assistant/query',
    }


def build_assistant_workspace(project, user) -> dict:
    requirements = (
        Requirement.query
        .filter_by(project_id=project.id)
        .order_by(Requirement.created_at.desc())
        .all()
    )
    summary = _requirements_summary(requirements)
    profile = _workspace_profile(user, project, summary)
    return {
        'assistant_summary': summary,
        'recent_requirements': requirements[:4],
        **profile,
    }


def answer_assistant_question(project, user, question: str) -> str:
    from app.rag import answer_question

    profession = (getattr(user, 'profession', None) or '').strip() if user.role == 'client' else None
    project_context = {
        'project_name': project.name,
        'project_status': project.status,
        'plot_zone': project.plot_zone,
    }
    return answer_question(
        question,
        audience=user.role,
        profession=profession or None,
        project_context=project_context,
    )
