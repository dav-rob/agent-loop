from agent_loop.goal_types import validate_goal_type


def goal_review_policy(goal_type: str) -> str:
    goal_type = validate_goal_type(goal_type)
    policies = {
        "prototype": """Goal type: prototype
Block only when the app cannot build or run, required verification fails, completed behavior materially regresses, or the central requested function is absent.
Security, architecture, portability, dependency upgrades, edge cases, and polish should still be noticed, but normally recorded as recommendations and must not cause another retry.""",
        "extend": """Goal type: extend
Block when the requested capability is absent, required verification fails, or existing working behavior materially regresses.
Improvements outside the requested extension belong in recommendations.""",
        "refine": """Goal type: refine
Block when the stated behavioral or usability outcome is not met, required verification fails, or existing behavior materially regresses.
Broader product and implementation improvements belong in recommendations.""",
        "repair": """Goal type: repair
Block when the target defect remains, its regression verification fails, or the repair causes a material regression.
Unrelated cleanup and improvement belong in recommendations.""",
        "harden": """Goal type: harden
Apply stricter security, reliability, testing, performance, and architecture standards within the explicitly stated scope.
Findings against those deliberate standards may block; unrelated expansion still belongs in recommendations.""",
        "investigate": """Goal type: investigate
Judge the quality of the evidence, whether the stated question was answered, and whether the conclusion follows from the evidence.
Runnable software is not required unless the goal explicitly requests it.""",
    }
    return policies[goal_type]
