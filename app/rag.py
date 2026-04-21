import logging
import os
import re
import sys
from pathlib import Path
from typing import List

FALLBACK_GUIDANCE = {
    'setback': {
        'aliases': ['setback', 'setbacks', 'front setback', 'side setback', 'rear setback'],
        'client_title': 'Setback',
        'client_explanation': 'Setback is the minimum open space you must keep between the building and the site boundary.',
        'client_bullets': [
            'It protects light, ventilation, safety access, and approval compliance.',
            'A quick default thumb rule in the local fallback guide is front >= 3 ft, rear >= 1.5 ft, and side >= 1.5 ft, but the final legal number depends on plot size, road width, and building type.',
        ],
        'architect_title': 'Setback Control',
        'architect_explanation': 'Setbacks define the mandatory clear offsets from the plot boundary and must be validated against road width, plot dimensions, and occupancy conditions.',
        'architect_bullets': [
            'Do not lock massing until the authority, road width, and plot-size-dependent clearance table are validated.',
            'Front, side, and rear setbacks should be checked together with height, FAR, and site coverage rather than in isolation.',
        ],
        'default_analogy': 'Think of setback like the breathing space a building must keep around itself so the site stays safe and compliant.',
        'profession_analogies': {
            'doctor': 'Think of setback like the sterile clearance around an operating field: if that safety zone is compromised, the whole procedure becomes risky.',
        },
    },
    'far': {
        'aliases': ['far', 'fsi', 'floor area ratio', 'floor space index'],
        'client_title': 'FAR / FSI',
        'client_explanation': 'FAR or FSI tells you how much total floor area can be built on a plot compared with the size of the site.',
        'client_bullets': [
            'If a 1,000 sq ft plot has an FAR of 1.5, the total permitted built-up floor area is roughly 1,500 sq ft across all floors.',
            'It works together with setbacks, road width, site coverage, and height limits. Meeting one control does not override the others.',
        ],
        'architect_title': 'FAR / FSI Control',
        'architect_explanation': 'FAR / FSI is the ratio of cumulative built-up area to plot area and governs total permissible floor area across storeys.',
        'architect_bullets': [
            'Validate FAR alongside coverage, height, and road-width-triggered controls before freezing massing.',
            'Use occupancy and authority context before treating a generic FAR number as submission-ready.',
        ],
        'default_analogy': 'Think of FAR like the total capacity limit for the site, not just the footprint of one floor.',
        'profession_analogies': {
            'doctor': 'Think of FAR like a safe dosage cap: the site is the patient, and FAR is the maximum total built-up dosage the plot can safely carry without overloading it.',
        },
    },
    'coverage': {
        'aliases': ['coverage', 'site coverage', 'ground coverage'],
        'client_title': 'Site Coverage',
        'client_explanation': 'Site coverage is the percentage of the plot that the building footprint can occupy at ground level.',
        'client_bullets': [
            'It controls how much open area stays available for circulation, light, drainage, and ventilation.',
            'Even when FAR allows more total floor area, coverage can still limit how wide the ground-floor footprint may become.',
        ],
        'architect_title': 'Site Coverage Control',
        'architect_explanation': 'Coverage caps the permissible ground-floor footprint as a percentage of the site area.',
        'architect_bullets': [
            'Coverage often becomes the horizontal control while FAR remains the total built-up control.',
            'Check coverage with setback geometry and parking/service clearances before assuming the footprint is buildable.',
        ],
        'default_analogy': 'Think of coverage like how much of the site you are allowed to place under the building’s shadow at ground level.',
        'profession_analogies': {
            'doctor': 'Think of coverage like how much of a treatment room can be occupied by equipment before staff circulation and safe access start suffering.',
        },
    },
    'frontage': {
        'aliases': ['frontage', 'front width'],
        'client_title': 'Frontage',
        'client_explanation': 'Frontage is the width of the plot facing the road.',
        'client_bullets': [
            'It affects access quality, design feasibility, and sometimes whether a proposal clears the minimum rule threshold.',
            'The local fallback guide uses a simple thumb rule of about 20 ft minimum frontage for a typical residential plot.',
        ],
        'architect_title': 'Frontage Check',
        'architect_explanation': 'Frontage is the road-facing plot width and often acts as a threshold condition for residential feasibility.',
        'architect_bullets': [
            'Frontage must be checked with access geometry, parking layout, and road classification before design freeze.',
        ],
        'default_analogy': 'Think of frontage as the width of the doorway through which the whole project meets the street.',
        'profession_analogies': {
            'doctor': 'Think of frontage like the width of a hospital access corridor: if it is too tight, every downstream movement becomes harder.',
        },
    },
    'area': {
        'aliases': ['plot area', 'minimum area', 'site area', 'area'],
        'client_title': 'Minimum Plot Area',
        'client_explanation': 'Plot area is the total size of the site, and some rule paths only apply if the plot crosses the minimum size threshold.',
        'client_bullets': [
            'A simple fallback thumb rule used here is about 600 sq ft as a minimum residential threshold.',
            'The exact approval path still depends on occupancy, authority, and the current regulation bundle.',
        ],
        'architect_title': 'Plot Area Threshold',
        'architect_explanation': 'Plot area is a threshold variable that influences the applicable compliance path, setbacks, and overall buildability.',
        'architect_bullets': [
            'Validate measured plot area against title and survey records before using it for compliance calculations.',
        ],
        'default_analogy': 'Think of plot area as the total working canvas available to the project.',
        'profession_analogies': {
            'doctor': 'Think of plot area like the total operating field available for a procedure: every later decision depends on that starting space.',
        },
    },
    'road_width': {
        'aliases': ['road width', 'abutting road', 'access road'],
        'client_title': 'Road Width',
        'client_explanation': 'Road width affects what can be approved on the site because access width influences height, safety, and serviceability.',
        'client_bullets': [
            'A wider road can support a broader approval envelope, while a tighter road can trigger stricter design limits.',
            'This is one of the first measurements your architect checks before promising height or area outcomes.',
        ],
        'architect_title': 'Road Width Trigger',
        'architect_explanation': 'Abutting road width is a primary trigger variable for buildability, especially for height and access-dependent controls.',
        'architect_bullets': [
            'Cross-check road-width evidence with document-backed proof before using it in compliance submissions.',
        ],
        'default_analogy': 'Think of road width like the size of the access lane feeding the project: it changes how much the site can realistically support.',
        'profession_analogies': {
            'doctor': 'Think of road width like ambulance access clearance: if the access corridor is too narrow, the overall treatment capacity drops.',
        },
    },
    'track': {
        'aliases': ['track a', 'track b', 'track'],
        'client_title': 'Track Classification',
        'client_explanation': 'Track classification is a way of grouping projects into different approval buckets based on size or type.',
        'client_bullets': [
            'The quick fallback guide uses Track A for smaller projects under roughly 1,500 sq ft and Track B for larger or commercial cases.',
            'Your architect still needs to confirm the live authority interpretation before submission.',
        ],
        'architect_title': 'Approval Track',
        'architect_explanation': 'Track classification changes the procedural path and should be validated against current authority conditions, not only size heuristics.',
        'architect_bullets': [
            'Use it as a workflow cue, then confirm the active approval process before client commitment.',
        ],
        'default_analogy': 'Think of track classification like being routed into different service lanes based on project size and complexity.',
        'profession_analogies': {
            'doctor': 'Think of track classification like triage routing: smaller, simpler cases and larger, more complex cases do not enter the same workflow lane.',
        },
    },
    'approval_risk': {
        'aliases': ['approval risk', 'approval risks', 'submission risk', 'rule risk', 'biggest rule risk'],
        'client_title': 'Approval Risk',
        'client_explanation': 'Approval risk means the part of the project most likely to trigger redesign, delay, or extra authority questions if it is not checked early.',
        'client_bullets': [
            'The usual early risk areas are setbacks, FAR or site coverage, road width, parking, and whether the drawings match the site documents.',
            'Ask your architect which one control could force the biggest redraw if it turns out to be wrong.',
        ],
        'architect_title': 'Approval Risk Review',
        'architect_explanation': 'Approval risk is the highest-likelihood mismatch between the current design, the site evidence, and the active submission rule path.',
        'architect_bullets': [
            'Validate trigger variables first: plot area, abutting road width, setbacks, site coverage, FAR, height, parking, and access-dependent controls.',
            'Treat drawing-to-document mismatch as a first-class risk: title, survey, road-width proof, and the submission set should all support the same geometry.',
        ],
        'default_analogy': 'Think of approval risk as the weakest checkpoint in the project path: if that one control fails, the whole submission slows down.',
    },
    'submission_readiness': {
        'aliases': ['before submission', 'validate before submission', 'submission readiness', 'submission-ready', 'likely tnpcr constraints'],
        'client_title': 'Pre-submission Checks',
        'client_explanation': 'Before submission, your team needs to confirm the main rule limits and make sure the paperwork supports the same design being shown in the drawings.',
        'client_bullets': [
            'The common checks are setbacks, total buildable area, site coverage, height, access road conditions, parking needs, and whether the document pack is complete.',
            'A project feels ready only when the design numbers and the supporting papers tell the same story.',
        ],
        'architect_title': 'Submission Readiness',
        'architect_explanation': 'Submission readiness means the governing rule path, trigger constraints, and document set have all been validated against the current design package.',
        'architect_bullets': [
            'Run a pre-submission sweep across plot dimensions, road width evidence, setbacks, FAR, coverage, height, parking or service requirements, and mandatory supporting documents.',
            'Freeze the filing set only after drawings, measurements, and ownership or authorization records align without contradiction.',
        ],
        'default_analogy': 'Think of submission readiness as making sure the design, measurements, and paperwork all lock together before you send the case forward.',
    },
    'document_blockers': {
        'aliases': ['document blockers', 'compliance documents', 'documents delay approval', 'architect-side blockers', 'approval documents'],
        'client_title': 'Document Blockers',
        'client_explanation': 'Approvals often slow down when the key project papers are missing, inconsistent, or do not match the current design.',
        'client_bullets': [
            'The usual blockers are land or authorization papers, survey-backed measurements, road or access proof, and drawing sets that are incomplete or out of sync.',
            'Ask which missing document would stop submission today if the team tried to file the project right now.',
        ],
        'architect_title': 'Document-side Blockers',
        'architect_explanation': 'Document blockers are the missing or inconsistent records that prevent the architect from filing a defensible submission set.',
        'architect_bullets': [
            'Common blockers include title or authorization gaps, survey or FMB mismatch, road-width evidence, incomplete drawing sheets, and missing technical or structural support documents.',
            'Check that the document pack supports the same site area, setbacks, access condition, and built-up figures shown on the drawings.',
        ],
        'default_analogy': 'Think of document blockers like missing case records: even a good design can stall if the evidence bundle is incomplete.',
    },
    'client_risk': {
        'aliases': ['risk to the client', 'communicate risks clearly', 'clean language', 'client-friendly risk'],
        'client_title': 'Rule Risk in Plain Language',
        'client_explanation': 'A good risk explanation should tell you what might go wrong, why it matters, and what the team is doing next.',
        'client_bullets': [
            'The clearest format is impact first, then cause, then action: what could be delayed, what rule is driving it, and what the architect will verify next.',
            'If a technical rule is still under review, ask what design or timeline effect it could have on the project.',
        ],
        'architect_title': 'Client Risk Framing',
        'architect_explanation': 'Client-side risk framing should translate a compliance issue into practical impact without overstating certainty.',
        'architect_bullets': [
            'Lead with impact, then cause, then action: explain the likely effect on approval, area, timeline, or redesign before describing the rule detail.',
            'Translate technical controls into client-facing consequences such as reduced buildable area, delayed submission, extra drawings, or revised cost and schedule expectations.',
        ],
        'default_analogy': 'Think of client risk framing as turning a technical warning into a clear next-step conversation.',
    },
}

FALLBACK_STOPWORDS = {
    'about', 'after', 'also', 'an', 'and', 'any', 'are', 'ask', 'assistant',
    'been', 'below', 'between', 'biggest', 'building', 'buildings', 'can', 'clean', 'could',
    'details', 'does', 'each', 'explain', 'for', 'from', 'give', 'guidance',
    'how', 'into', 'its', 'language', 'like', 'limits', 'minimum', 'most', 'need', 'now', 'please',
    'project', 'regulation', 'regulations', 'right', 'rule', 'rules', 'should', 'simple', 'simply',
    'technical', 'tell', 'than', 'that', 'the', 'their', 'them', 'there', 'these', 'this', 'those',
    'tnpcr', 'usually', 'what', 'when', 'which', 'with', 'your',
}

ADVISORY_FALLBACK_KEYS = {
    'approval_risk',
    'submission_readiness',
    'document_blockers',
    'client_risk',
}

_LOCAL_OPTIONAL_SITE_PACKAGES_READY = False


def _ensure_local_dependency_path():
    global _LOCAL_OPTIONAL_SITE_PACKAGES_READY
    if _LOCAL_OPTIONAL_SITE_PACKAGES_READY:
        return

    base_dir = Path(__file__).resolve().parents[1]
    version_dir = f'python{sys.version_info.major}.{sys.version_info.minor}'
    candidates = [
        base_dir / 'venv_clean' / 'lib' / version_dir / 'site-packages',
    ]

    for candidate in candidates:
        if candidate.exists():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)

    _LOCAL_OPTIONAL_SITE_PACKAGES_READY = True


def _resolve_regulation_pdfs(base_dir: str | Path) -> list[str]:
    root = Path(base_dir)
    pdfs: list[Path] = []

    primary = root / 'docs' / 'TNCDBR-2019.pdf'
    if primary.exists():
        pdfs.append(primary)

    uploads_dir = root / 'uploads'
    if uploads_dir.exists():
        upload_matches = sorted(
            uploads_dir.glob('*TNCDBR*.pdf'),
            key=lambda path: (
                'amendment' in path.name.lower(),
                -path.stat().st_size,
                path.name.lower(),
            ),
        )
        for path in upload_matches:
            if path not in pdfs:
                pdfs.append(path)

    return [str(path) for path in pdfs]


def _contains_alias(text: str, alias: str) -> bool:
    normalized = (text or '').lower()
    alias = alias.lower()
    if ' ' in alias:
        return alias in normalized
    return re.search(rf'\b{re.escape(alias)}\b', normalized) is not None


def _detect_guidance_key(question: str) -> str | None:
    lowered = (question or '').lower()
    best_key = None
    best_score = 0
    for key, data in FALLBACK_GUIDANCE.items():
        score = sum(1 for alias in data['aliases'] if _contains_alias(lowered, alias))
        if score > best_score:
            best_score = score
            best_key = key
    return best_key if best_score else None


def _professional_analogy_line(guidance_key: str | None, profession: str | None) -> str | None:
    if not guidance_key or not profession:
        return None
    guidance = FALLBACK_GUIDANCE.get(guidance_key) or {}
    profession_key = profession.strip().lower()
    if not profession_key:
        return None
    analogy = (guidance.get('profession_analogies') or {}).get(profession_key)
    if analogy:
        return analogy
    default_analogy = guidance.get('default_analogy')
    if not default_analogy:
        return None
    return f'Since you work as a {profession.strip()}, think of it this way: {default_analogy}'


def _project_context_lines(project_context: dict | None) -> list[str]:
    if not project_context:
        return []
    lines = []
    if project_context.get('project_name'):
        lines.append(f"Project: {project_context['project_name']}")
    if project_context.get('project_status'):
        lines.append(f"Stage: {project_context['project_status']}")
    if project_context.get('plot_zone'):
        lines.append(f"Plot Zone: {project_context['plot_zone']}")
    return lines


class RAGManager:
    _instance = None
    
    def __init__(self):
        self.vector_store = None
        base_dir = Path(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
        self.pdf_paths = _resolve_regulation_pdfs(base_dir)
        self.pdf_path = self.pdf_paths[0] if self.pdf_paths else str(base_dir / 'docs' / 'TNCDBR-2019.pdf')
        self.persist_directory = os.path.join(base_dir, 'instance', 'rag_db')
        self.handbook_chunks: list[str] = []
        self._anthropic_disabled = False
        self._gemini_disabled = False
        
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self):
        """Build the index if it doesn't exist."""
        if not self.handbook_chunks:
            self._load_handbook_chunks()

        _ensure_local_dependency_path()
        try:
            from langchain_community.document_loaders import PyPDFLoader
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            from langchain_huggingface import HuggingFaceEmbeddings
            from langchain_community.vectorstores import Chroma
        except Exception as exc:
            logging.warning(f"Vector RAG dependencies unavailable, using handbook text retrieval: {exc}")
            return bool(self.handbook_chunks)

        try:
            if not self.pdf_paths:
                logging.error(f"No regulation PDFs found for RAG. Expected docs/ or uploads/ TNCDBR files near: {self.pdf_path}")
                return bool(self.handbook_chunks)

            # Embeddings & VectorStore
            embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

            if os.path.exists(self.persist_directory) and os.listdir(self.persist_directory):
                self.vector_store = Chroma(
                    persist_directory=self.persist_directory,
                    embedding_function=embeddings
                )
                return True

            # Load and Split if no local cache
            docs = []
            for pdf_path in self.pdf_paths:
                loader = PyPDFLoader(pdf_path)
                docs.extend(loader.load())
            
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
            splits = text_splitter.split_documents(docs)
            
            self.vector_store = Chroma.from_documents(
                documents=splits,
                embedding=embeddings,
                persist_directory=self.persist_directory
            )
            return True
        except Exception as e:
            logging.warning(f"RAG vector init error, using handbook text retrieval: {e}")
            return bool(self.handbook_chunks)

    def query(
        self,
        question: str,
        *,
        audience: str = 'client',
        profession: str | None = None,
        project_context: dict | None = None,
    ) -> str:
        """Query the vector store and generate a role-aware response."""
        question = (question or '').strip()
        if not question:
            return 'Please enter a question.'

        guidance_key = _detect_guidance_key(question)
        context_chunks = []
        try:
            if not self.vector_store and not self.handbook_chunks:
                if not self.initialize():
                    return self._fallback_query(
                        question,
                        audience=audience,
                        profession=profession,
                        project_context=project_context,
                    )
            
            if self.vector_store:
                results = self.vector_store.similarity_search(question, k=6)
                if results:
                    context_chunks = [r.page_content for r in results if getattr(r, 'page_content', '').strip()]
            elif self.handbook_chunks and guidance_key not in ADVISORY_FALLBACK_KEYS:
                context_chunks = self._retrieve_handbook_chunks(question)
        except Exception as e:
            logging.error(f"Query error: {e}")

        if not context_chunks and self.handbook_chunks and guidance_key not in ADVISORY_FALLBACK_KEYS:
            context_chunks = self._retrieve_handbook_chunks(question)

        if context_chunks:
            llm_answer = self._query_with_gemini(
                question,
                context_chunks,
                audience=audience,
                profession=profession,
                project_context=project_context,
            )
            if llm_answer:
                return llm_answer

            llm_answer = self._query_with_anthropic(
                question,
                context_chunks,
                audience=audience,
                profession=profession,
                project_context=project_context,
            )
            if llm_answer:
                return llm_answer

            grounded_answer = self._context_only_answer(
                question,
                context_chunks,
                audience=audience,
                profession=profession,
                project_context=project_context,
            )
            if grounded_answer:
                return grounded_answer

        # If we reached here with no grounded context, try an ungrounded LLM reply
        # (prefer Gemini, then Anthropic) so the UI receives an informative answer
        # even when vector retrieval or handbook text is unavailable.
        try:
            if not context_chunks:
                llm_answer = self._query_with_gemini(
                    question,
                    [],
                    audience=audience,
                    profession=profession,
                    project_context=project_context,
                )
                if llm_answer:
                    return llm_answer

                llm_answer = self._query_with_anthropic(
                    question,
                    [],
                    audience=audience,
                    profession=profession,
                    project_context=project_context,
                )
                if llm_answer:
                    return llm_answer
        except Exception:
            # Be resilient — fall through to fallback guidance
            pass

        return self._fallback_query(
            question,
            audience=audience,
            profession=profession,
            project_context=project_context,
        )

    def _llm_prompt_context(
        self,
        *,
        audience: str = 'client',
        profession: str | None = None,
        project_context: dict | None = None,
    ) -> tuple[str, str, str, str]:
        audience = (audience or 'client').strip().lower()
        project_lines = _project_context_lines(project_context)
        project_text = '\n'.join(f'- {line}' for line in project_lines) if project_lines else '- Project context not provided'

        if audience == 'architect':
            audience_instruction = (
                'Your audience is an architect. Be technically precise, highlight approval risk, '
                'mention dependencies and compliance tradeoffs, and avoid casual analogies unless explicitly requested.'
            )
            style_instruction = (
                'Use crisp Markdown bullets and short sections. Prioritize actionable interpretation over reassurance.'
            )
            close_instruction = 'End with one short verification note about checking the latest authority interpretation before submission.'
        else:
            profession_line = (
                f'The client works as a {profession.strip()}. Use polished analogies from that profession when they help clarify a concept.'
                if profession else
                'The client is not a technical user. Explain things in plain professional language and use simple analogies when helpful.'
            )
            audience_instruction = (
                'Your audience is a client with no architectural background. Keep the explanation warm, clear, and non-jargon-heavy. '
                + profession_line
            )
            style_instruction = 'Use short Markdown bullets, plain English, and explain any technical term before using it.'
            close_instruction = 'End with a short reminder to confirm final decisions with their architect.'

        return audience_instruction, style_instruction, close_instruction, project_text

    def _query_with_anthropic(
        self,
        question: str,
        context_chunks: List[str],
        *,
        audience: str = 'client',
        profession: str | None = None,
        project_context: dict | None = None,
    ) -> str | None:
        if self._anthropic_disabled or not os.environ.get("ANTHROPIC_API_KEY"):
            return None

        try:
            import anthropic
        except Exception as exc:
            self._anthropic_disabled = True
            logging.warning(f"Anthropic import unavailable, skipping Claude grounded answer: {exc}")
            return None

        try:
            context = "\n\n".join(context_chunks)
            audience_instruction, style_instruction, close_instruction, project_text = self._llm_prompt_context(
                audience=audience,
                profession=profession,
                project_context=project_context,
            )
            prompt = f"""
You are the TNPCR (Tamil Nadu Planning Compliance) AI Assistant inside BuildSmart.
{audience_instruction}

Use the following retrieved context from the official regulations to ground your specific rules, numbers, and legal limits:

Context:
{context}

Project Context:
{project_text}

Question: {question}

Instructions:
1. Ground all numerical limits and legal interpretations in the retrieved Context.
2. If the Context does not define a basic term like setback or FAR, you may use general knowledge to explain the concept, but do not invent specific regulation numbers.
3. {style_instruction}
4. Format your response using Markdown for readability.
5. {close_instruction}

Helpful Answer:
""".strip()

            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            response = client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=1200,
                temperature=0.3,
                messages=[{'role': 'user', 'content': prompt}],
            )

            parts = []
            for block in getattr(response, 'content', []) or []:
                text = getattr(block, 'text', '')
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            answer = '\n\n'.join(parts).strip()
            return answer or None
        except Exception as exc:
            logging.warning(f"Claude grounded query failed, falling back: {exc}")
            return None

    def _query_with_gemini(
        self,
        question: str,
        context_chunks: List[str],
        *,
        audience: str = 'client',
        profession: str | None = None,
        project_context: dict | None = None,
    ) -> str | None:
        if self._gemini_disabled or not os.environ.get("GEMINI_API_KEY"):
            return None

        _ensure_local_dependency_path()
        try:
            # Ensure google genai client can read the key expected by some libs
            if os.environ.get('GEMINI_API_KEY') and not os.environ.get('GOOGLE_API_KEY'):
                os.environ['GOOGLE_API_KEY'] = os.environ.get('GEMINI_API_KEY')

            from langchain_core.prompts import PromptTemplate
            from langchain_google_genai import ChatGoogleGenerativeAI
        except Exception as exc:
            self._gemini_disabled = True
            logging.warning(f"Gemini import unavailable, using grounded fallback: {exc}")
            return None

        try:
            context = "\n\n".join(context_chunks)
            llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.3)
            audience_instruction, style_instruction, close_instruction, project_text = self._llm_prompt_context(
                audience=audience,
                profession=profession,
                project_context=project_context,
            )
            template = """
You are the TNPCR (Tamil Nadu Planning Compliance) AI Assistant inside BuildSmart.
{audience_instruction}

Use the following retrieved context from the official regulations to ground your specific rules, numbers, and legal limits:

Context:
{context}

Project Context:
{project_text}

Question: {question}

Instructions:
1. Ground all numerical limits and legal interpretations in the retrieved Context.
2. If the Context does not define a basic term like setback or FAR, you may use general knowledge to explain the concept, but do not invent specific regulation numbers.
3. {style_instruction}
4. Format your response using Markdown for readability.
5. {close_instruction}

Helpful Answer:
"""
            prompt = PromptTemplate.from_template(template)
            chain = prompt | llm
            response = chain.invoke({
                "audience_instruction": audience_instruction,
                "context": context,
                "project_text": project_text,
                "question": question,
                "style_instruction": style_instruction,
                "close_instruction": close_instruction,
            })
            content = getattr(response, 'content', '')
            return content.strip() if isinstance(content, str) and content.strip() else None
        except Exception as exc:
            self._gemini_disabled = True
            logging.warning(f"Gemini query failed, using grounded fallback: {exc}")
            return None

    def _context_only_answer(
        self,
        question: str,
        context_chunks: List[str],
        *,
        audience: str = 'client',
        profession: str | None = None,
        project_context: dict | None = None,
    ) -> str | None:
        snippets = self._rank_context_snippets(question, context_chunks)
        if not snippets:
            return None

        guidance_key = _detect_guidance_key(question)
        guidance = FALLBACK_GUIDANCE.get(guidance_key) if guidance_key else None
        bullets = '\n'.join(f'- {snippet}' for snippet in snippets)
        project_lines = _project_context_lines(project_context)
        project_section = '\n'.join(f'- {line}' for line in project_lines)

        if (audience or 'client').strip().lower() == 'architect':
            sections = []
            if guidance:
                sections.extend([
                    f"**{guidance['architect_title']}**",
                    guidance['architect_explanation'],
                    "",
                    *[f"- {bullet}" for bullet in guidance['architect_bullets']],
                ])
            if bullets:
                if sections:
                    sections.append("")
                sections.extend([
                    "Handbook-grounded notes:",
                    bullets,
                ])
            if project_section:
                sections.extend(["", "Project framing:", project_section])
            sections.extend([
                "",
                "Next step:",
                "- Validate the live authority interpretation and document set before submission.",
            ])
            return '\n'.join(sections)

        analogy_line = _professional_analogy_line(guidance_key, profession)
        sections = []
        if guidance:
            sections.extend([
                f"**{guidance['client_title']}**",
                guidance['client_explanation'],
                "",
                *[f"- {bullet}" for bullet in guidance['client_bullets']],
            ])
        if bullets:
            if sections:
                sections.append("")
            sections.extend([
                "Handbook-grounded notes:",
                bullets,
            ])
        if project_section:
            sections.extend(["", "Your project snapshot:", project_section])
        if analogy_line:
            sections.extend(["", "Professional analogy:", f"- {analogy_line}"])
        sections.extend([
            "",
            "If you want, ask a more specific follow-up such as setback, plot area, FAR, or road-width limits.",
            "",
            "Please confirm the final interpretation with your architect.",
        ])
        return '\n'.join(sections)

    def _rank_context_snippets(self, question: str, context_chunks: List[str], limit: int = 3) -> List[str]:
        tokens = self._keyword_tokens(question)
        sentences = []
        for chunk in context_chunks:
            normalized_chunk = re.sub(r'\s+', ' ', chunk or '').strip()
            if not normalized_chunk:
                continue
            for sentence in re.split(r'(?<=[.!?])\s+|\n+', normalized_chunk):
                text = re.sub(r'\s+', ' ', sentence).strip(' -\t')
                if len(text) < 28:
                    continue
                sentences.append(text)

        if not sentences:
            return []

        ranked = []
        for sentence in sentences:
            lowered = sentence.lower()
            score = sum(1 for token in tokens if token in lowered)
            if score == 0 and not tokens:
                score = 1
            if score > 0:
                ranked.append((score, len(sentence), sentence))

        if not ranked:
            return []

        unique = []
        seen = set()
        for _, _, sentence in sorted(ranked, key=lambda item: (-item[0], item[1])):
            compact = sentence[:260].rstrip(' ,;:')
            key = compact.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(compact)
            if len(unique) >= limit:
                break
        return unique

    def _load_handbook_chunks(self) -> bool:
        if self.handbook_chunks:
            return True

        _ensure_local_dependency_path()
        try:
            from pypdf import PdfReader
        except Exception as exc:
            logging.error(f"PDF reader unavailable for handbook retrieval: {exc}")
            return False

        if not self.pdf_paths:
            logging.error("No regulation PDFs are available for handbook retrieval.")
            return False

        chunks: list[str] = []
        for pdf_path in self.pdf_paths:
            try:
                reader = PdfReader(pdf_path)
                for page_index, page in enumerate(reader.pages):
                    text = re.sub(r'\s+', ' ', page.extract_text() or '').strip()
                    if len(text) < 80:
                        continue
                    prefix = f'{Path(pdf_path).stem} page {page_index + 1}: '
                    chunks.extend(self._chunk_text(prefix + text))
            except Exception as exc:
                logging.warning(f"Could not parse handbook PDF {pdf_path}: {exc}")

        self.handbook_chunks = chunks
        return bool(self.handbook_chunks)

    def _chunk_text(self, text: str, chunk_size: int = 1200, overlap: int = 200) -> List[str]:
        cleaned = re.sub(r'\s+', ' ', text or '').strip()
        if not cleaned:
            return []
        if len(cleaned) <= chunk_size:
            return [cleaned]

        chunks = []
        step = max(chunk_size - overlap, 200)
        start = 0
        while start < len(cleaned):
            end = min(len(cleaned), start + chunk_size)
            chunk = cleaned[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(cleaned):
                break
            start += step
        return chunks

    def _retrieve_handbook_chunks(self, question: str, limit: int = 6) -> List[str]:
        if not self.handbook_chunks:
            return []

        tokens = self._keyword_tokens(question)
        guidance_key = _detect_guidance_key(question)
        scored = []
        for chunk in self.handbook_chunks:
            lowered = chunk.lower()
            score = 0
            for token in tokens:
                if re.search(rf'\b{re.escape(token)}\b', lowered):
                    score += 2
                elif token in lowered:
                    score += 1
            if guidance_key and any(_contains_alias(lowered, alias) for alias in FALLBACK_GUIDANCE[guidance_key]['aliases']):
                score += 2
            if score > 0:
                scored.append((score, len(chunk), chunk))

        if not scored:
            return []

        top_chunks = []
        seen = set()
        for _, _, chunk in sorted(scored, key=lambda item: (-item[0], item[1])):
            compact = chunk.strip()
            key = compact.lower()
            if key in seen:
                continue
            seen.add(key)
            top_chunks.append(compact)
            if len(top_chunks) >= limit:
                break
        return top_chunks

    def _keyword_tokens(self, text: str) -> List[str]:
        tokens = []
        for token in re.findall(r'[a-z0-9]+', (text or '').lower()):
            if len(token) < 3 or token in FALLBACK_STOPWORDS:
                continue
            tokens.append(token)
        return tokens

    def _fallback_query(
        self,
        question: str,
        *,
        audience: str = 'client',
        profession: str | None = None,
        project_context: dict | None = None,
    ) -> str:
        guidance_key = _detect_guidance_key(question)
        if guidance_key:
            guidance = FALLBACK_GUIDANCE[guidance_key]
            project_lines = _project_context_lines(project_context)
            if (audience or 'client').strip().lower() == 'architect':
                bullets = '\n'.join(f"- {bullet}" for bullet in guidance['architect_bullets'])
                sections = [
                    f"**{guidance['architect_title']}**",
                    guidance['architect_explanation'],
                    "",
                    bullets,
                ]
                if project_lines:
                    sections.extend(["", "**Project Framing**", *[f"- {line}" for line in project_lines]])
                sections.extend([
                    "",
                    "- Cross-check the latest authority-specific interpretation before submission.",
                ])
                return '\n'.join(sections)

            bullets = '\n'.join(f"- {bullet}" for bullet in guidance['client_bullets'])
            analogy_line = _professional_analogy_line(guidance_key, profession)
            sections = [
                f"**{guidance['client_title']}**",
                guidance['client_explanation'],
                "",
                bullets,
            ]
            if project_lines:
                sections.extend(["", "**Your Project Context**", *[f"- {line}" for line in project_lines]])
            if analogy_line:
                sections.extend(["", "**Professional Analogy**", f"- {analogy_line}"])
            sections.extend([
                "",
                "Please confirm the final design decision with your architect.",
            ])
            return '\n'.join(sections)

        if (audience or 'client').strip().lower() == 'architect':
            return (
                "I could not ground a precise handbook answer for that question yet.\n\n"
                "- Try asking with the exact control name such as setback, FAR, site coverage, frontage, or road width.\n"
                "- Cross-check the live authority circulars and project documents before relying on a generic interpretation."
            )
        return (
            "I could not find a clear handbook-grounded answer for that question yet.\n\n"
            "- Try asking with the exact term like setback, FAR, site coverage, frontage, or road width.\n"
            "- Please confirm the final answer with your architect before acting on it."
        )

    def extract_requirement_intent(self, caption: str) -> str:
        """Extracts structured intent/tags from a user's image caption."""
        if not caption:
            return "No specific intent provided."

        prompt = (
            "Analyze this architectural reference image caption and extract the core design intent "
            "in 3-5 keywords. Output only the keywords separated by commas.\n\n"
            f"Caption: {caption}"
        )

        _ensure_local_dependency_path()
        if os.environ.get("GEMINI_API_KEY"):
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                from langchain_core.prompts import PromptTemplate

                llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.1)
                template = """
                Analyze this architectural reference image caption and extract the core design intent in 3-5 keywords.
                Caption: {caption}
                Output ONLY the keywords separated by commas.
                """
                prompt_template = PromptTemplate.from_template(template)
                chain = prompt_template | llm
                response = chain.invoke({"caption": caption})
                content = getattr(response, 'content', '')
                if isinstance(content, str) and content.strip():
                    return content.strip()
            except Exception as e:
                logging.error(f"Gemini intent extraction failed: {e}")

        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                import anthropic

                client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
                response = client.messages.create(
                    model='claude-sonnet-4-6',
                    max_tokens=120,
                    temperature=0.1,
                    messages=[{'role': 'user', 'content': prompt}],
                )
                parts = []
                for block in getattr(response, 'content', []) or []:
                    text = getattr(block, 'text', '')
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                extracted = ', '.join(parts).strip()
                if extracted:
                    return extracted
            except Exception as e:
                logging.error(f"Anthropic intent extraction failed: {e}")

        return "General Reference"

def answer_question(
    question,
    *,
    audience: str = 'client',
    profession: str | None = None,
    project_context: dict | None = None,
):
    return RAGManager.get_instance().query(
        question,
        audience=audience,
        profession=profession,
        project_context=project_context,
    )


def query(question):
    return answer_question(question)


# ─────────────────────────────────────────────────────────────────────────────
# NLP Pipeline 2 — Structured requirement intent extraction
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_chunks(text, top_k=4):
    """Return top_k relevant TNPCR text chunks for the given query."""
    try:
        mgr = RAGManager.get_instance()
        if not mgr.vector_store:
            mgr.initialize()
        if mgr.vector_store:
            results = mgr.vector_store.similarity_search(text, k=top_k)
            return [r.page_content for r in results]
    except Exception as e:
        logging.warning(f"[rag] retrieve_chunks failed: {e}")
    return []


def _response_to_text(response):
    """Normalize model responses from LangChain or Anthropic into plain text."""
    content = getattr(response, 'content', response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str) and block.strip():
                parts.append(block.strip())
                continue
            text = getattr(block, 'text', '')
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return '\n\n'.join(parts).strip()
    return ''


def _strip_markdown_fences(text):
    clean = (text or '').strip()
    if clean.startswith('```'):
        lines = [line for line in clean.split('\n') if not line.strip().startswith('```')]
        clean = '\n'.join(lines).strip()
    return clean


def _call_gemini(prompt, temperature=0.1):
    """Call Gemini via Google Generative AI. Reads GEMINI_API_KEY from env."""
    _ensure_local_dependency_path()
    # Expose GEMINI_API_KEY as GOOGLE_API_KEY for libraries that check either
    if os.environ.get('GEMINI_API_KEY') and not os.environ.get('GOOGLE_API_KEY'):
        os.environ['GOOGLE_API_KEY'] = os.environ.get('GEMINI_API_KEY')

    from langchain_google_genai import ChatGoogleGenerativeAI

    llm = ChatGoogleGenerativeAI(model='gemini-2.5-flash', temperature=temperature)
    response = llm.invoke(prompt)
    return _response_to_text(response)


def _call_claude(prompt):
    """Call Claude Sonnet via Anthropic API. Reads ANTHROPIC_API_KEY from env."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    message = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1024,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return _response_to_text(message)


def _get_compliance_context(project):
    """Extract plot compliance data for the NLP prompt."""
    if not project:
        return {}
    context = {'project_name': project.name, 'status': project.status}
    try:
        from app.models import PlotAnalysis
        analysis = (PlotAnalysis.query
                    .filter_by(project_id=project.id)
                    .order_by(PlotAnalysis.created_at.desc())
                    .first())
        if analysis and analysis.input_payload:
            import json as _json
            inputs = _json.loads(analysis.input_payload)
            context.update({
                'plot_area_sqft': inputs.get('plot_area'),
                'road_width_ft': inputs.get('road_width'),
                'front_setback_ft': inputs.get('front_setback'),
                'height_m': inputs.get('building_height'),
                'far': inputs.get('far'),
                'trust_level': analysis.trust_level,
            })
    except Exception:
        pass
    return context


def extract_requirement_intent(caption, project_id):
    """
    NLP Pipeline 2 — Report Section 4.5.
    Uses Gemini first with Claude fallback + existing RAG vector store.
    Returns dict with all 5 required keys from report.
    """
    import json

    if not caption or not caption.strip():
        return {
            'intent_summary': 'No caption provided',
            'spatial_features': [],
            'compliance_conflicts': [],
            'feasibility_score': 50,
            'architect_note': 'No caption — upload an image with a description for AI analysis.',
        }

    if not os.environ.get('GEMINI_API_KEY') and not os.environ.get('ANTHROPIC_API_KEY'):
        return {
            'intent_summary': caption[:200],
            'spatial_features': [],
            'compliance_conflicts': [],
            'feasibility_score': 50,
            'architect_note': 'No live AI API key set — skipping NLP.',
        }

    # Project compliance context
    try:
        from app.models import Project
        project = Project.query.get(project_id)
        compliance_summary = _get_compliance_context(project)
    except Exception:
        compliance_summary = {}

    # TNPCR RAG context (graceful fallback if vector store not ready)
    context = 'No specific TNPCR regulation retrieved.'
    try:
        chunks = retrieve_chunks(caption, top_k=4)
        if chunks:
            context = '\n'.join(chunks)
    except Exception:
        pass

    prompt = f"""You are a Tamil Nadu construction compliance expert analyzing a client's design requirement.

Client requirement caption: {caption}

TNPCR regulatory context: {context}

Project plot data: {json.dumps(compliance_summary)}

Extract ONLY a JSON object with these exact keys:
{{
  "intent_summary": "one clear sentence summarizing what the client wants",
  "spatial_features": ["list", "of", "specific", "spatial", "features", "mentioned"],
  "compliance_conflicts": ["list of potential TNPCR rule conflicts detected, empty list if none"],
  "feasibility_score": 80,
  "architect_note": "one practical actionable note for the architect"
}}

Rules:
- feasibility_score must be an integer between 0 and 100
- compliance_conflicts must be a list of strings, can be empty []
- spatial_features must be a list of strings
- Return ONLY the JSON object, no markdown code blocks, no explanation, no preamble
    """

    try:
        response_text = ''
        if os.environ.get('GEMINI_API_KEY'):
            try:
                response_text = _call_gemini(prompt, temperature=0.1)
            except Exception as exc:
                logging.error(f"[rag] Gemini extract_requirement_intent failed: {exc}")

        if not response_text and os.environ.get('ANTHROPIC_API_KEY'):
            response_text = _call_claude(prompt)

        clean = _strip_markdown_fences(response_text)

        result = json.loads(clean)

        # Ensure all required keys and correct types
        if not isinstance(result.get('spatial_features'), list):
            result['spatial_features'] = []
        if not isinstance(result.get('compliance_conflicts'), list):
            result['compliance_conflicts'] = []
        if not isinstance(result.get('feasibility_score'), (int, float)):
            result['feasibility_score'] = 75

        for key, default in [
            ('intent_summary', caption[:200]),
            ('spatial_features', []),
            ('compliance_conflicts', []),
            ('feasibility_score', 75),
            ('architect_note', 'See analysis above.'),
        ]:
            result.setdefault(key, default)

        return result

    except Exception as e:
        logging.error(f"[rag] extract_requirement_intent error: {e}")
        return {
            'intent_summary': caption[:150] if caption else 'Caption provided',
            'spatial_features': [],
            'compliance_conflicts': [],
            'feasibility_score': 50,
            'architect_note': f'Manual review required — AI extraction error: {str(e)[:80]}',
        }
