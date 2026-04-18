"""Admin scoring service helpers used by HTTP route modules."""

from fastapi import HTTPException


async def run_admin_score_test(
    query,
    target,
    include_details=True,
    logger=None,
    score_calculator=None,
    risk_level_getter=None,
):
    """Run the admin scoring comparison and shape the response payload."""
    if score_calculator is None:
        from utils.idf_scoring import calculate_comprehensive_score

        score_calculator = calculate_comprehensive_score
    if risk_level_getter is None:
        from utils.idf_scoring import get_risk_level

        risk_level_getter = get_risk_level

    try:
        result = score_calculator(
            query,
            target,
            include_details=include_details,
        )

        final_score = result.get("final_score", 0)
        factor_data = result.get("factors", {})

        factors = {
            "raw_similarity": round(result.get("raw_score", 0), 4),
            "word_match_factor": round(factor_data.get("word_match", 0), 4),
            "length_ratio_factor": round(factor_data.get("length_ratio", 0), 4),
            "coverage_factor": round(factor_data.get("coverage", 0), 4),
            "idf_factor": round(factor_data.get("idf", 0), 4),
            "combined_factor": round(result.get("combined_factor", 0), 4),
        }

        if include_details and "details" in result:
            factors["word_details"] = result["details"]

        risk = risk_level_getter(final_score)

        return {
            "query": query,
            "target": target,
            "final_score": round(final_score, 4),
            "final_score_pct": f"{final_score * 100:.1f}%",
            "risk_level": risk,
            "factors": factors,
        }
    except Exception as exc:
        if logger:
            logger.error(f"Test scoring error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
