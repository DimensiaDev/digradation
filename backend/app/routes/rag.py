from fastapi import APIRouter

from app.deps import CurrentUser, EmbeddingProviderDep

router = APIRouter(
    prefix="/rag",
    tags=["rag"],
    dependencies=[],
)


@router.get("/health")
async def rag_health(
    _: CurrentUser,
    provider: EmbeddingProviderDep,
) -> dict:
    """Report the configured embedding model.

    Lets operators confirm the provider wired correctly without triggering
    an API call. Indexing routes and the pgvector store land in the next PR.
    """
    return {
        "model": provider.model,
        "dimension": provider.dimension,
    }
