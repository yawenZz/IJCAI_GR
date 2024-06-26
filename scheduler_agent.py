import copy
import random
import string
import sys
import plan
import torch
import numpy as np
import constants
from agent import Agent
from typing import Iterable, List
from cooperative_craft_world import UP, DOWN, LEFT, RIGHT, COLLECT, CRAFT, NO_OP, _reward, _rewardable_items, CooperativeCraftWorldState
from dqn import DQN_Config
from policy import Policy, SoftmaxPolicy
from vector_2d import Vector2D


tiny_val = 1e-6


class MCTS_Node(object):

    def __init__(self, top_level_plan, target, action, n_agents):
        self.top_level_plan = top_level_plan
        self.target = target
        self.action = action
        self.n_agents = n_agents
        self.children = None
        self.visits = 0
        self.total_return = [0] * n_agents
        self.sim_self_actions = []
        self.sim_external_actions = []

    def is_leaf(self):
        return self.children is None

    def expand(self, possible_actions_by_goal_plan):
        if len(possible_actions_by_goal_plan) > 0:
            self.children = []
            for possible_goal_plan, possible_action in possible_actions_by_goal_plan.items():
                self.children.append(MCTS_Node(possible_goal_plan[0], possible_goal_plan[1], possible_action, self.n_agents))

    def update_sim_actions(self, player_actions):
        self.sim_self_actions = player_actions

    def update_sim_external_actions(self, external_agent_actions):
        self.sim_external_actions = external_agent_actions

class MCTS_Node_Subgoal_Level(object):

    def __init__(self, target, n_agents):
        self.target = target
        self.n_agents = n_agents
        self.children = None
        self.visits = 0
        self.total_return = [0] * n_agents
        self.sim_self_actions = []
        self.sim_external_actions = []

    def is_leaf(self):
        return self.children is None
    
    def expand(self, possible_targets):
        if len(possible_targets) > 0:
            self.children = []
            for target in possible_targets:
                self.children.append(MCTS_Node_Subgoal_Level(target, self.n_agents))

    def update_sim_actions(self, player_actions):
        self.sim_self_actions = player_actions

    def update_sim_external_actions(self, external_agent_actions):
        self.sim_external_actions = external_agent_actions

    def display_tree_structure(self, depth=0):
        if self.children is not None:
            print("  " * depth, f"Target: {self.target}, Visits: {self.visits}, Return: {self.total_return}, ExternalActions: {self.sim_external_actions}")
            for child in self.children:
                child.display_tree_structure(depth + 1)
        else:
            print("  " * depth, f"Leaf Node - Target: {self.target}, Visits: {self.visits}, Return: {self.total_return}, ExternalActions: {self.sim_external_actions}")

class SchedulerAgent(Agent):

    def __init__(self, name, mcts_style:int, goal_recogniser, num_targets_per_item=1, single_player=False, psychic=False, alpha=100, beta=10, gamma=0.99, c=np.sqrt(2.0),
        extra_rollout_stochasticity=0, external_agent_rollout_policy:Policy=SoftmaxPolicy(0.005), external_agent_config:DQN_Config=None):

        self.mcts_style = mcts_style
        self.allegiance = constants.SELFISH # Default value, overridden by python_agent.py
        self.goal_recogniser = goal_recogniser
        self.external_agent_rollout_policy = external_agent_rollout_policy
        self.num_targets_per_item = num_targets_per_item
        self.single_player = single_player
        self.psychic = psychic
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.c = c
        self.extra_rollout_stochasticity = extra_rollout_stochasticity
        self.external_agent_config = external_agent_config
        self.model_choice_idx = -1

        self.self_actions = []
        self.sim_self_actions = []
        self.sim_external_agent_actions = []

        if external_agent_config is not None:
            self.device = torch.device("cuda" if self.external_agent_config.gpu >= 0 else "cpu")
        
        super().__init__(name)


    def get_allegiance(self):
        if self.single_player:
            return constants.SELFISH
        else:
            return self.allegiance


    def reset(self, agent_num, seed, goal_set, externally_visible_goal_sets):

        super().reset(agent_num, seed, goal_set, externally_visible_goal_sets)

        self.target = None
        self.target_type = None
        self.plans:List[plan.Plan] = []
        for item in self.goal_set:
            self.plans.append(plan.str_to_plan(item, self.num_targets_per_item))

        self.self_actions = []
        self.sim_self_actions = []
        self.sim_external_agent_actions = []

    def get_possible_targets(self, state):
        targets = []
        for plan in self.plans:
            targets = targets + plan.getNextTargets(state, self.num_targets_per_item)

        # Discard duplicates
        targets = list(set(targets))

        return targets


    def get_possible_goal_plans(self, state):

        result = {}
        for plan in self.plans:
            possible_targets = plan.getNextTargets(state, self.num_targets_per_item)
            if len(possible_targets) > 0:
                result[plan] = possible_targets
            
        return result


    def choose_random_goal_plan(self, state : CooperativeCraftWorldState):

        possible_goal_plans = self.get_possible_goal_plans(state)

        if len(possible_goal_plans) > 0:

            choices = []
            for possible_goal, possible_targets in possible_goal_plans.items():
                for target in possible_targets:
                    choices.append((possible_goal, target, state.get_object_type_at_square(target)))

            return choices[random.randrange(len(choices))]

        else:
            return (None, None, None)


    def choose_random_target(self, state : CooperativeCraftWorldState):

        possible_targets = self.get_possible_targets(state)

        if len(possible_targets) > 0:
            target = possible_targets[random.randrange(len(possible_targets))]
            target_type = state.get_object_type_at_square(target)
        else:
            target = None
            target_type = None

        return (target, target_type)


    def choose_next_target(self, state, possible_targets):
        print("---choose_next_target()---")

        # Can take shortcut if there not multiple possible targets.
        if len(possible_targets) == 0:
            # update simulated actions for the agent
            state_copy = copy.deepcopy(state)
            _, _, player_actions, external_agent_actions = self.rollout(state_copy)  # Run the rollout
            self.sim_self_actions = player_actions
            self.sim_external_agent_actions = external_agent_actions

            return None, None
        elif len(possible_targets) == 1:
            best_target = list(possible_targets)[0]

            # update simulated actions for the agent
            state_copy = copy.deepcopy(state)
            _, _, player_actions, external_agent_actions = self.rollout(state_copy)  # Run the rollout
            self.sim_self_actions = player_actions
            self.sim_external_agent_actions = external_agent_actions

            return best_target, state.get_object_type_at_square(best_target)

        if self.beta == 0:
            random_target = possible_targets[random.randrange(len(possible_targets))]

            # update simulated actions for the agent
            state_copy = copy.deepcopy(state)
            _, _, player_actions, external_agent_actions = self.rollout(state_copy)  # Run the rollout
            self.sim_self_actions = player_actions
            self.sim_external_agent_actions = external_agent_actions

            return random_target, state.get_object_type_at_square(random_target)

        # --== Main MCTS algorithm begins here ==--
        print("-----MCTS-----")

        root_node = MCTS_Node_Subgoal_Level(None, state.n_agents)
        n_rollouts = 0

        for _ in range(0, self.alpha):

            self.model_choice_idx = -1

            if self.goal_recogniser is not None:
                self.goal_recogniser.update_hypothesis()
                assumed_reward_func = self.get_assumed_reward_func()
            else:
                assumed_reward_func = _reward

            state_copy = copy.deepcopy(state)
            current_node = root_node
            visited = []
                # We don't actually need to backpropagate anything to the root node;
                # only its children matter for final action selection.
            returns = []
            steps_taken = []

            can_look_further = True
            # when can_look_further is True & current node is root -
            while can_look_further and not current_node.is_leaf():
                current_node = self.select(current_node, state_copy.player_turn, n_rollouts)
                visited.append(current_node)

                target = current_node.target
                target_type = state_copy.get_object_type_at_square(target)

                steps_before = state_copy.steps
                state_copy, ret, can_look_further = self.look_ahead(state_copy, target, target_type, assumed_reward_func)

                returns.append(ret)
                steps_taken.append(state_copy.steps - steps_before)

            current_node.expand(self.get_possible_targets(state_copy))

            # when current node is root -
            if not current_node.is_leaf():
                current_node = self.select(current_node, state_copy.player_turn, n_rollouts)
                visited.append(current_node)
                
                target = current_node.target
                target_type = state_copy.get_object_type_at_square(target)

                steps_before = state_copy.steps
                state_copy, ret, can_look_further = self.look_ahead(state_copy, target, target_type, assumed_reward_func)

                returns.append(ret)
                steps_taken.append(state_copy.steps - steps_before)

            for _ in range(0, self.beta):

                # Run the rollout.
                _, rollout_return, player_actions, external_agent_actions = self.rollout(state_copy)
                n_rollouts += 1

                # Backpropagate the returns.
                for n in range(len(visited) - 1, -1, -1):  # 倒序遍历：从最后一个visited开始，从后往前
                    visited[n].visits += 1
                    # update the corresponding simulation action list
                    visited[n].update_sim_actions(player_actions)
                    visited[n].update_sim_external_actions(external_agent_actions)
                    for agent_idx in range(0, state_copy.n_agents):

                        # Update the backpropagated return for the previous node visited.
                        rollout_return[agent_idx] = returns[n][agent_idx] + (self.gamma ** steps_taken[n]) * rollout_return[agent_idx]

                        visited[n].total_return[agent_idx] += rollout_return[agent_idx]

        # root_node.display_tree_structure()

        # --== Select the most promising child of the root node ==--
        selected = None
        best_eval = float('-inf')

        for choice in root_node.children:

            # Note: This currently assumes that there are only two agents in the environment.
            eval = (choice.total_return[self.agent_num] + self.get_allegiance() * choice.total_return[1 - self.agent_num]) / (choice.visits + tiny_val) \
                + tiny_val * random.random() # For tie-breaking
   
            if eval > best_eval:
                selected = choice
                best_eval = eval

        # update value for the agent
        self.sim_self_actions = selected.sim_self_actions
        self.sim_external_agent_actions = selected.sim_external_actions

        return selected.target, state.get_object_type_at_square(selected.target)


    def get_sim_action(self, state : CooperativeCraftWorldState, top_level_plan : plan.Plan, target : Vector2D, target_type : string):

        if self.mcts_style in (constants.SUBGOAL_LEVEL_COMMIT, constants.SUBGOAL_LEVEL_NON_COMMITTAL):

            action, target, target_type = get_action_from_target(state, target, target_type)

            if action is not None:
                return None, action, target, target_type

            # If we've reached this point then the target was either reached or invalidated, so choose a new target.
            target, target_type = self.choose_random_target(state)

            # If we can't find a new target, there's nothing to do.
            if target is None:
                return None, NO_OP, None, None
            else:
                action, target, target_type = get_action_from_target(state, target, target_type)
                return None, action, target, target_type

        elif self.mcts_style == constants.I_RM: # The rollouts for I_RM are at the top level, since interleaved intentions are effectively terminated.

            if top_level_plan is None:
                top_level_plan, target, target_type = self.choose_random_goal_plan(state)

                # If we can't find a new top-level plan, there's nothing to do.
                if top_level_plan is None:
                    return (None, NO_OP, None, None)

            # If the previous subgoal (target) was completed, or hasn't been set yet, then it's time to set a new one.
            if target is None:

                # Try for consistency with top_level_plan if possible.
                possible_goal_plans = self.get_possible_goal_plans(state)
                if top_level_plan in possible_goal_plans:
                    possible_targets = possible_goal_plans[top_level_plan]
                else:
                    possible_targets = []

                if len(possible_targets) == 0:
                    # There's no possible way to continue with the current top-level plan, so reset the top-level goal.
                    return self.get_sim_action(state, None, None, None)

                else:
                    target = possible_targets[random.randrange(len(possible_targets))]
                    target_type = state.get_object_type_at_square(target)

            action, target, target_type = get_action_from_target(state, target, target_type)

            # If the previous subgoal (target) was completed, get_action_from_target will return action == None. Need to reset the target if this is the case.
            if action is None:
                        
                if target is not None:
                    input("An unexpected error occured! (Action is none but target is not.) Press ENTER to exit the program.")
                    sys.exit(0)

                return self.get_sim_action(state, top_level_plan, None, None)

            return top_level_plan, action, target, target_type

        else:
            input("ERROR: Unexpected MCTS style (" + str(self.mcts_style) + ")! Press ENTER to exit the program.")
            sys.exit(0)


    def perceive(self, reward:float, state:CooperativeCraftWorldState, terminal:bool, is_eval:bool):

        # simulate external agent's action in case of hypo is None
        if self.goal_recogniser.current_hypothesis is None:
            self.goal_recogniser.update_hypothesis()

        if self.mcts_style == constants.I_RM:

            a = self.get_action_atomic(state)

        else:

            if self.mcts_style == constants.SUBGOAL_LEVEL_COMMIT:
                a, self.target, self.target_type = get_action_from_target(state, self.target, self.target_type)
            else:
                a, self.target, self.target_type = None, None, None

            # If we just reached our current target (or the target was invalidated), choose a new one.
            if a is None:
                targets = self.get_possible_targets(state)
                self.target, self.target_type = self.choose_next_target(state, targets)
                a, self.target, self.target_type = get_action_from_target(state, self.target, self.target_type)

                if a is None:
                    return random.randrange(state.action_space.n)

        # store the action
        self.self_actions.append(a)

        return a


    def select(self, current_node, player_turn : int, n_rollouts : int):

        selected = None
        best_UCT = float('-inf')
        
        # Calculate the UCT value for each child node
        for child_node in current_node.children:

            # UCT calculation
            # Note: This currently assumes that there are only two agents in the environment.
            uct_value = (child_node.total_return[player_turn] + self.get_allegiance() * child_node.total_return[1 - player_turn]) / (child_node.visits + tiny_val) \
                + self.c * np.sqrt(np.log(n_rollouts + 1) / (child_node.visits + tiny_val)) \
                + tiny_val * random.random() # For tie-breaking
            
            if uct_value > best_UCT:
                selected = child_node
                best_UCT = uct_value
        
        return selected


    def get_filtered_actions(self, state : CooperativeCraftWorldState) -> Iterable[int]:

        possible_targets = self.get_possible_targets(state)
        possible_actions = set()

        for target in possible_targets:
            a, _, _ = get_action_from_target(state, target, state.get_object_type_at_square(target))
            possible_actions.add(a)

        return sorted(list(possible_actions))


    def get_possible_actions_by_goal_plan(self, state : CooperativeCraftWorldState):

        result = {}   
        possible_goal_plans = self.get_possible_goal_plans(state)

        for possible_goal, possible_targets in possible_goal_plans.items():
            for target in possible_targets:
                a, _, _ = get_action_from_target(state, target, state.get_object_type_at_square(target))
                result[(possible_goal, target)] = a
            
        return result


    def get_action_atomic(self, state : CooperativeCraftWorldState) -> int:
        print("---get_action_atomic()---")

        possible_actions = self.get_filtered_actions(state)

        # Can take shortcut if there not multiple possible actions.
        if len(possible_actions) == 0:
            # update simulated actions for the agent
            state_copy = copy.deepcopy(state)
            _, _, player_actions, external_agent_actions = self.rollout(state_copy)  # Run the rollout
            self.sim_self_actions = player_actions
            self.sim_external_agent_actions = external_agent_actions

            return random.randrange(state.action_space.n)
        elif len(possible_actions) == 1:
            # update simulated actions for the agent
            state_copy = copy.deepcopy(state)
            _, _, player_actions, external_agent_actions = self.rollout(state_copy)  # Run the rollout
            self.sim_self_actions = player_actions
            self.sim_external_agent_actions = external_agent_actions

            return list(possible_actions)[0]

        if self.beta == 0:
            # update simulated actions for the agent
            state_copy = copy.deepcopy(state)
            _, _, player_actions, external_agent_actions = self.rollout(state_copy)  # Run the rollout
            self.sim_self_actions = player_actions
            self.sim_external_agent_actions = external_agent_actions

            possible_actions = list(possible_actions)
            return possible_actions[random.randrange(len(possible_actions))]

        # --== Main MCTS algorithm begins here ==--
        print("-----MCTS-----")

        root_node = MCTS_Node(None, None, None, state.n_agents)
        n_rollouts = 0

        for alpha_iter in range(0, self.alpha):

            self.model_choice_idx = -1

            if self.goal_recogniser is not None:
                self.goal_recogniser.update_hypothesis()
                assumed_reward_func = self.get_assumed_reward_func()
            else:
                assumed_reward_func = _reward

            state_copy = copy.deepcopy(state)
            current_node = root_node
            current_top_level_plan = None
            current_target = None
            visited = []
                # We don't actually need to backpropagate anything to the root node;
                # only its children matter for final action selection.
            rewards = []

            # Selection phase: Navigate down the tree
            while not current_node.is_leaf():
                current_node = self.select(current_node, state_copy.player_turn, n_rollouts)
                if current_node.top_level_plan != plan._none_plan:
                    current_top_level_plan = current_node.top_level_plan
                    current_target = current_node.target

                visited.append(current_node)
                r = state_copy.step(current_node.action, assumed_reward_func=assumed_reward_func)

                rewards.append(r)

            # Expansion phase: Expand the tree node
            if state_copy.player_turn == self.agent_num:
                expansion_possibilities = self.get_possible_actions_by_goal_plan(state_copy)

                if not constants.I_RM_TREE_POLICY_INTERLEAVE:
                    # Narrow the expansion possibilities based on the top-level goal currently being pursued.
                    if (current_top_level_plan is not None) and (current_target is not None) and (current_top_level_plan, current_target) in expansion_possibilities:
                        expansion_possibilities = {(current_top_level_plan, current_target) : expansion_possibilities[(current_top_level_plan, current_target)]}

                current_node.expand(expansion_possibilities)
                
            else:
                # The external agent isn't limited to actions from our plans.
                if self.single_player:
                    current_node.expand({(plan._none_plan, None) : [NO_OP]})
                else:
                    current_node.expand({(plan._none_plan, None) : list(range(0, state_copy.action_space.n))})

            if not current_node.is_leaf():
                current_node = self.select(current_node, state_copy.player_turn, n_rollouts)
                if current_node.top_level_plan != plan._none_plan:
                    current_top_level_plan = current_node.top_level_plan
                    current_target = current_node.target

                visited.append(current_node)
                r = state_copy.step(current_node.action, assumed_reward_func=assumed_reward_func)

                rewards.append(r)

            # Simulation phase: Simulate a rollout from the current node
            for _ in range(0, self.beta):
                
                # Run the rollout.
                _, rollout_return, player_actions, external_agent_actions  = self.rollout(state_copy, top_level_plan=current_top_level_plan, target=current_target, print_debug=(alpha_iter > (self.alpha - 3)))
                n_rollouts += 1

                # Backpropagation phase: Backpropagate the returns.
                for n in range(len(visited) - 1, -1, -1):
                    visited[n].visits += 1
                    # update the corresponding simulation action list
                    visited[n].update_sim_actions(player_actions)
                    visited[n].update_sim_external_actions(external_agent_actions)
                    for agent_idx in range(0, state_copy.n_agents):

                        # Update the backpropagated return for the previous node visited.
                        rollout_return[agent_idx] = rewards[n][agent_idx] + self.gamma * rollout_return[agent_idx]

                        visited[n].total_return[agent_idx] += rollout_return[agent_idx]

        # --== Select the most promising child of the root node ==--
        selected = None
        best_eval = float('-inf')

        for choice in root_node.children:

            # Note: This currently assumes that there are only two agents in the environment.
            # 根据rollout更新的total return来计算eval值，选出最大的结果
            eval = (choice.total_return[self.agent_num] + self.get_allegiance() * choice.total_return[1 - self.agent_num]) / (choice.visits + tiny_val) \
                + tiny_val * random.random() # For tie-breaking
   
            if eval > best_eval:
                selected = choice
                best_eval = eval

        # update value for the agent
        self.sim_self_actions = selected.sim_self_actions
        self.sim_external_agent_actions = selected.sim_external_actions

        return selected.action


    def get_assumed_reward_func(self):

        assumed_reward_func = []

        for i in range(0, len(_reward)):
            if i == self.agent_num:
                assumed_reward_func.append(_reward[i])
            else:
                if self.psychic:
                    other_agent_goals = self.goal_recogniser.other_agent.goal
                else:
                    other_agent_goals = self.goal_recogniser.current_hypothesis

                other_agent_goals = other_agent_goals.split("_and_")

                other_agent_reward = {}

                for item in _rewardable_items:
                    if item in other_agent_goals:
                        other_agent_reward[item] = 1
                    else:
                        other_agent_reward[item] = 0

                assumed_reward_func.append(other_agent_reward)

        return assumed_reward_func


    def get_external_agent_sim_action(self, state: object) -> int:

        if self.single_player:
            return NO_OP

        if self.external_agent_config is not None:
            # prepare the environment state
            state = torch.from_numpy(state.getRepresentation()).float().to(self.device).unsqueeze(0)

            if self.psychic:
                q = self.goal_recogniser.models[self.goal_recogniser.other_agent.goal].forward(state).cpu().detach().squeeze()
            else:
                # --- Section 5 Algorithm --- #
                # Assume that the agent will follow the hypothesised goal with the greatest Q-value.
                hypothesis_array = self.goal_recogniser.current_hypothesis.split("_and_")
                if len(hypothesis_array) == 1:
                    q = self.goal_recogniser.models[hypothesis_array[0]].forward(state).cpu().detach().squeeze()
                else:
                    # Switch single-goal policies based on the logic described in Section 5 of the paper.
                    if self.model_choice_idx == -1:
                        self.model_choice_idx = random.randrange(len(hypothesis_array))

                    q = self.goal_recogniser.models[hypothesis_array[self.model_choice_idx]].forward(state).cpu().detach().squeeze()

                    # If Qmax < threshold, switch to pursuing the goal that currently has the greatest Q-value.
                    if q.max().item() < 0.5:
                        for i in range(0, len(hypothesis_array)):
                            q_tmp = self.goal_recogniser.models[hypothesis_array[i]].forward(state).cpu().detach().squeeze()
                            if q_tmp.max().item() > q.max().item():
                                q = q_tmp
                                self.model_choice_idx = i
            
            action, _ = self.external_agent_rollout_policy.sample_action(q)

            return action

        else:
            return random.randrange(state.action_space.n)


    def look_ahead(self, state : CooperativeCraftWorldState, target : Vector2D, target_type : string, assumed_reward_func):

        state_copy = copy.deepcopy(state)
        target_copy = copy.deepcopy(target)
        target_type_copy = copy.deepcopy(target_type)
        steps_forward = 0
        ret = [0] * state.n_agents

        while not state_copy.terminal:

            if state_copy.player_turn == self.agent_num:
                action, target_copy, target_type_copy = get_action_from_target(state_copy, target_copy, target_type_copy)
                if action is None:
                    break
            else:
                action = self.get_external_agent_sim_action(state_copy)

            reward = state_copy.step(action, assumed_reward_func=assumed_reward_func)

            for i in range(0, state_copy.n_agents):
                ret[i] += (self.gamma ** steps_forward) * reward[i]

            steps_forward += 1

        can_look_further = not state_copy.terminal
        new_p_pos = state_copy.objects["player"][self.agent_num]
        if new_p_pos != target:
            can_look_further = False

        return (state_copy, ret, can_look_further)


    def rollout(self, state : CooperativeCraftWorldState, top_level_plan=None, target=None, print_debug=False):
        """
        Return:
            tuple: the simulated final state, and the cumulative rewards for each agent.
        """
        state_copy = copy.deepcopy(state)
        player_actions = []  # 保存玩家选择的action
        external_agent_actions = []  # 保存external agent选择的action

        if target is None:
            target_type = None
        else:
            target_type = state.get_object_type_at_square(target)
            
        steps_forward = 0
        ret = [0] * state.n_agents

        while not state_copy.terminal:

            if np.random.uniform() < self.extra_rollout_stochasticity:
                action = random.randrange(state.action_space.n)
            elif state_copy.player_turn == self.agent_num:
                top_level_plan, action, target, target_type = self.get_sim_action(state_copy, top_level_plan, target, target_type)
                player_actions.append(action)  # 记录玩家选择的action
            else:
                action = self.get_external_agent_sim_action(state_copy)
                external_agent_actions.append(action)  # 记录external agent选择的action

            reward = state_copy.step(action)

            for i in range(0, state_copy.n_agents):
                ret[i] += (self.gamma ** steps_forward) * reward[i]

            steps_forward += 1

        return (state_copy, ret, player_actions, external_agent_actions)

    def my_get_external_agent_sim_action(self, state: object) -> List[int]:
        if self.single_player:
            return []

        if self.external_agent_config is not None:
                state_copy = copy.deepcopy(state)
                sim_agent = copy.deepcopy(self)  # treat current agent as the sim agent
                # Get a list of possible goals (hypotheses)
                hypothesis_array = self.goal_recogniser.current_hypothesis.split("_and_")
                sim_agent.goal_set = hypothesis_array
                sim_agent.plans: List[plan.Plan] = []
                for item in sim_agent.goal_set:
                    sim_agent.plans.append(plan.str_to_plan(item, sim_agent.num_targets_per_item))
                # Get possible actions
                possible_targets = sim_agent.get_possible_targets(state)
                possible_actions = set()
                for target in possible_targets:
                    a, _, _ = get_action_from_target(state_copy, target, state.get_object_type_at_square(target))
                    possible_actions.add(a)
                possible_actions = sorted(list(possible_actions))

                # Can take shortcut if there not multiple possible actions
                if (len(possible_actions) == 0 ) or (len(possible_actions) == 1):
                    _, _, player_actions, external_agent_actions = sim_agent.rollout(state_copy)  # Run the rollout
                    return player_actions

                if sim_agent.beta == 0:
                    _, _, player_actions, external_agent_actions = sim_agent.rollout(state_copy)  # Run the rollout
                    sim_agent.sim_self_actions = player_actions
                    sim_agent.sim_external_agent_actions = external_agent_actions
                    return player_actions

                # --== Main MCTS algorithm begins here ==--
                root_node = MCTS_Node(None, None, None, state.n_agents)
                n_rollouts = 0

                for alpha_iter in range(0, sim_agent.alpha):

                    sim_agent.model_choice_idx = -1

                    if sim_agent.goal_recogniser is not None:
                        sim_agent.goal_recogniser.update_hypothesis()
                        assumed_reward_func = sim_agent.get_assumed_reward_func()
                    else:
                        assumed_reward_func = _reward

                    state_copy = copy.deepcopy(state)
                    current_node = root_node
                    current_top_level_plan = None
                    current_target = None
                    visited = []
                    # We don't actually need to backpropagate anything to the root node;
                    # only its children matter for final action selection.
                    rewards = []

                while not current_node.is_leaf():
                    current_node = sim_agent.select(current_node, state_copy.player_turn, n_rollouts)
                    if current_node.top_level_plan != plan._none_plan:
                        current_top_level_plan = current_node.top_level_plan
                        current_target = current_node.target

                    visited.append(current_node)
                    r = state_copy.step(current_node.action, assumed_reward_func=assumed_reward_func)

                    rewards.append(r)

                if state_copy.player_turn == sim_agent.agent_num:
                    expansion_possibilities = sim_agent.get_possible_actions_by_goal_plan(state_copy)

                    if not constants.I_RM_TREE_POLICY_INTERLEAVE:
                        # Narrow the expansion possibilities based on the top-level goal currently being pursued.
                        if (current_top_level_plan is not None) and (current_target is not None) and (
                        current_top_level_plan, current_target) in expansion_possibilities:
                            expansion_possibilities = {
                                (current_top_level_plan, current_target): expansion_possibilities[
                                    (current_top_level_plan, current_target)]}

                    current_node.expand(expansion_possibilities)

                else:
                    # The external agent isn't limited to actions from our plans.
                    if sim_agent.single_player:
                        current_node.expand({(plan._none_plan, None): [NO_OP]})
                    else:
                        current_node.expand({(plan._none_plan, None): list(range(0, state_copy.action_space.n))})

                if not current_node.is_leaf():
                    current_node = sim_agent.select(current_node, state_copy.player_turn, n_rollouts)
                    if current_node.top_level_plan != plan._none_plan:
                        current_top_level_plan = current_node.top_level_plan
                        current_target = current_node.target

                    visited.append(current_node)
                    r = state_copy.step(current_node.action, assumed_reward_func=assumed_reward_func)

                    rewards.append(r)

                # Simulation phase: Simulate a rollout from the current node
                for _ in range(0, sim_agent.beta):

                    # Run the rollout.
                    _, rollout_return, player_actions, external_agent_actions = sim_agent.rollout(state_copy,
                                                                                             top_level_plan=current_top_level_plan,
                                                                                             target=current_target,
                                                                                             print_debug=(
                                                                                                         alpha_iter > (
                                                                                                             sim_agent.alpha - 3)))
                    n_rollouts += 1

                    # Backpropagate the returns.
                    for n in range(len(visited) - 1, -1, -1):
                        visited[n].visits += 1
                        # update the corresponding simulation action list
                        visited[n].update_sim_actions(player_actions)
                        visited[n].update_sim_external_actions(external_agent_actions)
                        for agent_idx in range(0, state_copy.n_agents):
                            # Update the backpropagated return for the previous node visited.
                            rollout_return[agent_idx] = rewards[n][agent_idx] + sim_agent.gamma * rollout_return[
                                agent_idx]

                            visited[n].total_return[agent_idx] += rollout_return[agent_idx]

                # After simulations, select the most promising child from the root node
                selected = None
                best_eval = float('-inf')

                for choice in root_node.children:

                    # Note: This currently assumes that there are only two agents in the environment.
                    # 根据rollout更新的total return来计算eval值，选出最大的结果
                    eval = (choice.total_return[sim_agent.agent_num] + sim_agent.get_allegiance() * choice.total_return[
                        1 - sim_agent.agent_num]) / (choice.visits + tiny_val) \
                           + tiny_val * random.random()  # For tie-breaking

                    if eval > best_eval:
                        selected = choice
                        best_eval = eval

                return sim_agent.sim_self_actions

        else:
            return [random.randrange(state.action_space.n)]

# Returns a 3-tuple representing <action, updated_target, updated_target_type>
def get_action_from_target(state : CooperativeCraftWorldState, target : Vector2D, target_type : string):

    p_pos = state.objects["player"][state.player_turn]

    if target is None:
        return (None, None, None)

    # Check if target was invalidated.
    if state.get_object_type_at_square(target) != target_type:
        return (None, None, None)

    # Check if the player has reached the target.
    elif p_pos.x == target.x and p_pos.y == target.y:

        if target_type in ("gem", "gold", "grass", "iron", "wood"):
            action = COLLECT
        elif target_type in ("workbench", "toolshed", "factory"):
            action = CRAFT
        else:
            action = None

        return (action, None, None)

    # 水平位置更大：左右移动
    elif abs(p_pos.x - target.x) > abs(p_pos.y - target.y):
        if p_pos.x < target.x:
            return (RIGHT, target, target_type)
        elif p_pos.x > target.x:
            return (LEFT, target, target_type)

    # 垂直距离更大：上下移动
    else:
        if p_pos.y < target.y:
            return (UP, target, target_type)
        elif p_pos.y > target.y:
            return (DOWN, target, target_type)
