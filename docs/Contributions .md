# Team Contributions

## Kenil Sarang
- Set up the base project structure, folder layout, and environment configuration
- Built the static analysis engine and call graph builder to map function relationships across the codebase
- Implemented the embedding pipeline to convert code and docs into vector representations
- Created the background job queue using Redis and RQ so heavy indexing work runs without blocking the API
- Wrote the indexing service that coordinates all six phases of the pipeline end to end
- Built the FastAPI backend exposing all REST endpoints for auth, chat, indexing, and query logs
- Wrote the project documentation including README, architecture guide, setup instructions, and usage reference

## Shriram
- Designed the full database schema and implemented all SQLAlchemy ORM models
- Built the repository layer for users, documents, indexing sessions, conversation history, and developer profiles
- Implemented JWT based authentication including password hashing, token generation, and the login and register flow
- Integrated Vertex AI for LLM text generation and text embeddings using the Gemini model
- Added Cohere reranking to improve retrieval quality after vector search
- Built the AI agent system using Google ADK including the orchestrator, retrieval agent, answer agent, and validation agent

## Shauraya
- Set up the Qdrant vector store wrapper with named code and doc vectors per the system design
- Built the code chunking pipeline with Tree-sitter for syntax aware chunking across multiple languages
- Added fallback chunking strategies for markdown and fixed size windows so every file type is handled
- Implemented the LLM doc generation pipeline producing architecture, module, and function level documentation
- Built the GitHub MCP integration and webhook handler for automatic doc regeneration on every PR merge
- Developed the complete React frontend including login, chat, indexing, and query log views