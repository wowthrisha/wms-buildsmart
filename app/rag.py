import os
import logging
from typing import List, Optional
from flask import current_app

# Default rules for fallback
RULES = {
    'setback': "Setback is the minimum distance from plot boundary. Front ≥ 3ft, Rear ≥ 1.5ft, Side ≥ 1.5ft.",
    'frontage': "Minimum frontage of 20ft required for residential plots.",
    'area': "Minimum 600 sq ft for residential building approval.",
    'track': "Track A (under 1500 sq ft) or Track B (larger/commercial).",
}

class RAGManager:
    _instance = None
    
    def __init__(self):
        self.vector_store = None
        base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        self.pdf_path = os.path.join(base_dir, "docs", "TNCDBR-2019.pdf")
        self.persist_directory = os.path.join(base_dir, 'instance', 'rag_db')
        
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self):
        """Build the index if it doesn't exist."""
        try:
            from langchain_community.document_loaders import PyPDFLoader
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            from langchain_huggingface import HuggingFaceEmbeddings
            from langchain_community.vectorstores import Chroma
            
            if not os.path.exists(self.pdf_path):
                logging.error(f"PDF not found: {self.pdf_path}")
                return False

            # Embeddings & VectorStore
            embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
            
            if os.path.exists(self.persist_directory) and os.listdir(self.persist_directory):
                self.vector_store = Chroma(
                    persist_directory=self.persist_directory,
                    embedding_function=embeddings
                )
                return True

            # Load and Split if no local cache
            loader = PyPDFLoader(self.pdf_path)
            docs = loader.load()
            
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
            splits = text_splitter.split_documents(docs)
            
            self.vector_store = Chroma.from_documents(
                documents=splits,
                embedding=embeddings,
                persist_directory=self.persist_directory
            )
            return True
        except Exception as e:
            logging.error(f"RAG init error: {e}")
            return False

    def query(self, question: str) -> str:
        """Query the vector store and generate a structured response using Gemini."""
        try:
            if not self.vector_store:
                if not self.initialize():
                    return self._fallback_query(question)
            
            # Retrieve relevant chunks
            results = self.vector_store.similarity_search(question, k=6)
            if results:
                context = "\n\n".join([r.page_content for r in results])
                
                # Pass to Gemini for structured response
                from langchain_google_genai import ChatGoogleGenerativeAI
                from langchain_core.prompts import PromptTemplate
                import os
                
                if not os.environ.get("GEMINI_API_KEY"):
                    return "ERROR: GEMINI_API_KEY environment variable is not set."
                
                llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0.3)
                
                template = """
You are the TNPCR (Tamil Nadu Planning Compliance) AI Assistant. Your audience is a client who is a LAYMAN with NO architectural, building, or approval knowledge.

Your goal is to explain building rules simply, clearly, and using helpful analogies (like medical or everyday analogies) when requested or when it makes complex concepts easier to understand.

Use the following retrieved context from the official regulations to ground your specific rules, numbers, and legal limits:

Context:
{context}

Question: {question}

Instructions:
1. Explain the answer in simple, easy-to-understand layman terms.
2. If the user asks for an analogy, creatively provide one to explain the concept clearly.
3. If the retrieved context doesn't explicitly define a basic architectural term (like "setback", "FSI", etc.), you MAY use your general knowledge to explain the basic concept, but you MUST rely strictly on the Context for actual TNPCR numerical rules and legal limits.
4. Format your response using Markdown (bullet points, bold text) for readability.
5. Always end your response with a brief reminder to consult their architect for final verification.

Helpful Answer:
"""
                prompt = PromptTemplate.from_template(template)
                chain = prompt | llm
                
                response = chain.invoke({"context": context, "question": question})
                return response.content
        except Exception as e:
            logging.error(f"Query error: {e}")
            
        return self._fallback_query(question)

    def _fallback_query(self, question: str) -> str:
        q = question.lower()
        for kw, ans in RULES.items():
            if kw in q: return ans
        return "I couldn't find a specific rule in the documents. Please consult your architect."

    def extract_requirement_intent(self, caption: str) -> str:
        """Extracts structured intent/tags from a user's image caption."""
        if not caption:
            return "No specific intent provided."
        
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.prompts import PromptTemplate
            
            if not os.environ.get("GEMINI_API_KEY"):
                return "General Reference"

            llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0.1)
            template = """
            Analyze this architectural reference image caption and extract the core design intent in 3-5 keywords.
            Caption: {caption}
            Output ONLY the keywords separated by commas.
            """
            prompt = PromptTemplate.from_template(template)
            chain = prompt | llm
            response = chain.invoke({"caption": caption})
            return response.content.strip()
        except Exception as e:
            logging.error(f"Intent Extraction Failed: {e}")
            return "General Reference"

def query(question):
    return RAGManager.get_instance().query(question)


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


def _call_claude(prompt):
    """Call Claude Sonnet via Anthropic API. Reads ANTHROPIC_API_KEY from env."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    message = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1024,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return message.content[0].text


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
    Uses Claude Sonnet via Anthropic API + existing RAG vector store.
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

    if not os.environ.get('ANTHROPIC_API_KEY'):
        return {
            'intent_summary': caption[:200],
            'spatial_features': [],
            'compliance_conflicts': [],
            'feasibility_score': 50,
            'architect_note': 'ANTHROPIC_API_KEY not set — skipping NLP.',
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
        response_text = _call_claude(prompt)

        # Strip markdown fences if Claude added them
        clean = response_text.strip()
        if clean.startswith('```'):
            lines = [l for l in clean.split('\n') if not l.strip().startswith('```')]
            clean = '\n'.join(lines).strip()

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
