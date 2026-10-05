from openai import OpenAI
import json
import requests
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_classic.chains import RetrievalQA
from langchain_classic.prompts import PromptTemplate
import streamlit as st

st.set_page_config(
    page_title="PM Knowledge Assistant",
    page_icon="🤖",
    layout="centered"
)
st.title("🤖 PM Knowledge Assistant")



api_key=""
def load_qa_chain():
    loader=TextLoader("C:\\Users\\Bindu\\OneDrive\\Desktop\\AI\\final\\project\\pm_notes.txt")
    documents=loader.load()


    splitter=RecursiveCharacterTextSplitter(
    chunk_size=500,  # Each chunk can contain about 200 characters
    chunk_overlap=50  # The next chunk repeats 30 characters from the previous chunk
    )

    chunks=splitter.split_documents(documents)

#print(chunks[0].page_content)

    embeddings = OpenAIEmbeddings(api_key="") 
    vectorstore=Chroma.from_documents(chunks, embeddings)

# Better prompt — reduces hallucination
    prompt_template = """
    You are a helpful PM assistant.
    Use ONLY the context below to answer the question.
    If the answer is not in the context, say exactly:
    "I don't have that information in my documents."
    Do not make up any answers.

    Context:
    {context}

    Question: {question}

#Answer:"""

    PROMPT = PromptTemplate(
    template=prompt_template,
    input_variables=["context", "question"]
)

    llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=""
    )

    return RetrievalQA.from_chain_type(
    llm=llm,
    chain_type="stuff",
    retriever=vectorstore.as_retriever(
        search_kwargs={"k": 3}
    ),
    chain_type_kwargs={"prompt": PROMPT},
    return_source_documents=True
    )

with st.spinner("⏳ Loading AI system..."):
    qa_chain = load_qa_chain()

    st.success("✅ System ready!")
    st.divider()

# Question input
question = st.text_input("❓ Ask a question about your projects:")
result = qa_chain.invoke({"query": question})
st.subheader("💡 Answer:")
st.write(result["result"])

st.divider()
st.caption("Built with LangChain + ChromaDB + OpenAI + Streamlit | Bindu Singh")
