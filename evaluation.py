def simple_similarity_f(action1, action2):
    """
    Simple similarity function that compares whether two action indices are the same.

    Args:
    action1: Index of the first action.
    action2: Index of the second action.

    Returns:
    float: 1.0 if the actions are the same, otherwise 0.0.
    """
    return 1.0 if action1 == action2 else 0.0


def evaluation_function(actions1, actions2, similarity_function, alpha):
    """
    Evaluate the accumulated similarity for two sequences of actions based on f

    Args:
        actions1 (list): Sequence of actions for the first agent.
        actions2 (list): Sequence of actions for the second agent.
        similarity_function (function): Evaluation function f to compute similarity ∈ [0, 1] between two actions.
        alpha (float): Decay factor.

    Returns:
        float: Accumulated similarity between the two action sequences.
    """

    if not actions1 or not actions2:
        return 0.0

    # Determine the length of the shorter sequence
    # TODO: 根据neutral_2的结果看，paried agent最终action长度会有不变化的情况
    min_length = min(len(actions1), len(actions2))

    # Base case: If one of the sequences is empty, return 0
    if min_length == 0:
        return 0.0
    # if len(actions1) == 1 or len(actions2) == 1:
    #     return similarity_function(actions1[0], actions2[0])

    # Calculate similarity for the first action pair
    similarity = alpha * similarity_function(actions1[0], actions2[0])

    # Recursively calculate similarity for the rest of the sequences
    remaining_similarity = evaluation_function(actions1[1:min_length], actions2[1:min_length], similarity_function, alpha)
    # remaining_similarity = evaluation_function(actions1[1:], actions2[1:], similarity_function, alpha)

    # Apply exponential decay
    accumulated_similarity = similarity + (1 - alpha) * remaining_similarity

    return accumulated_similarity

### Test
# if __name__ == "__main__":
#     actions1 = [0, 1, 2, 3, 2, 5, 8]
#     actions2 = [0, 1, 3, 4, 2, 6, 8]
#     alpha = 0.7  # TODO: Decay factor
#
#     accumulated_similarity = evaluation_function(actions1, actions2, simple_similarity_f, alpha)
#     print("Accumulated Similarity:", accumulated_similarity)
