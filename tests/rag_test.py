import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.rag import RAGManager

def test_rag_flow():
    rag = RAGManager.get_instance()
    
    print("--- 1. Testing RAG Initialization ---")
    success = rag.initialize()
    if not success:
        print("❌ RAG Initialization Failed (Check if PDF exists and libs are installed)")
        return
    print("✅ RAG Initialization Successful")
    
    print("\n--- 2. Testing Logic Queries ---")
    queries = [
        "What are the rules for High Rise Buildings?",
        "What is the minimum frontage for a residential plot?",
        "Tell me about setbacks in TNPCR 2019."
    ]
    
    for q in queries:
        print(f"Query: {q}")
        ans = rag.query(q)
        print(f"Answer: {ans[:200]}...") # Print first 200 chars
        print("-" * 20)

if __name__ == "__main__":
    test_rag_flow()
