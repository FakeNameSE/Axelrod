from axelrod.actions import Actions, Action
from axelrod.player import Player


C, D = Actions.C, Actions.D


class DBS(Player):
    """
    Desired Belief Strategy as described in [Au2006]_
    http://www.cs.utexas.edu/%7Echiu/papers/Au06NoisyIPD.pdf

    A strategy that learns the opponent's strategy, and uses symbolic 
    noise detection for detecting whether anomalies in player’s behavior
    are deliberate or accidental, hence increasing performance in noisy 
    tournaments.  

    From the learned opponent's strategy, a tree search is used to
    choose the best move

    Default values for the parameters are the suggested values in the
    article. When noise increases you can try to diminish 
    violation_threshold and rejection_threshold

    Parameters
    
    discount_factor : float, optional
        used when computing discounted frequencies to learn opponent's
        strategy. Must be between 0 and 1. The default is 0.75
    promotion_threshold : int, optional 
        number of successive observations needed to promote an
        opponent behavior as a deterministic rule. The default is 3.
    violation_threshold : int, optional
        number of observations needed to considerate opponent's 
        strategy has changed. You can lower it when noise increases.
        The default is 4, which is good for a noise level of .1 
    reject_threshold : int, optional
        number of observations before forgetting opponent's previous
        strategy. You can lower it when noise increases. The default 
        is 3, which is good for a noise level of .1
    tree_depth: int, optional
        depth of the tree for the tree-search algorithm. Higher depth
        means more time to compute the move. The default is 5
    """

    # These are various properties for the strategy
    name = 'DBS'
    classifier = {
        'memory_depth': float('inf'),
        'stochastic': False,
        'makes_use_of': set(),
        'long_run_time': False,
        'inspects_source': False,
        'manipulates_source': False,
        'manipulates_state': False
    }

    def __init__(self, discount_factor=.75, promotion_threshold=3, 
                 violation_threshold=4, reject_threshold=3, tree_depth=5): 
        super().__init__()
        
        # The opponent's behavior is represented by a 3 dicts :
        # Rd, Rc, and Rp.
        # His behavior his modeled by a set of rules. A rule is the move that 
        # the opponent will play (C or D or a probability to play C) after a 
        # given outcome (for instance after (C, D))
        # A rule can be deterministic or probabilistic
        # - Rc is the set of deterministic rules
        # - Rp is the set of probabilistic rules
        # - Rd is the default rule set which is used for initialization but also
        # keeps track of previous policies when change in the opponent behavior
        # happens, in order to have a smooth transition
        # - Pi is a set of rules that aggregates all above sets of rules in 
        # order to fully model the opponent's behavior

        # Default rule set Rd
        # Default opponent's policy is TitForTat
        self.Rd = create_policy(1, 1, 0, 0)
        # Set of current deterministic rules Rc
        self.Rc = {}
        # Aggregated rule set Pi
        self.Pi = self.Rd   
        # For each rule in Rd we need to count the number of successive 
        # violations. Those counts are saved in violation_counts.
        self.violation_counts = {}
        self.reject_threshold = reject_threshold
        self.violation_threshold = violation_threshold
        self.promotion_threshold = promotion_threshold
        self.tree_depth = tree_depth
        # v is a violation count used to know when to clean the default rule
        # set Rd
        self.v = 0
        # A discount factor for computing the probabilistic rules
        self.alpha = discount_factor

        # The probabilistic rule set Rp is not saved as an attribute, but each 
        # rule is computed only when needed.
        # The rules are computed as discounted frequencies of opponent's past
        # moves. To compute the discounted frequencies, we need to keep
        # up to date an history of what has been played following each
        # outcome (or condition):
        # We save it as a dict history_by_cond; keys are conditions 
        # (ex (C, C)) and values are a tuple of 2 lists (G, F)
        # for a condition j and an iteration i in the match : 
        # G[i] = 1 if cond j was True at turn i-1 and C has been played
        # by the opponent; else G[i] = 0
        # F[i] = 1 if cond j was True at turn i-1; else F[i]=0
        # this representation makes the computing of discounted frequencies
        # easy and efficient
        # initial hypothesized policy is TitForTat
        self.history_by_cond = {
            (C, C): ([1], [1]),
            (C, D): ([1], [1]),
            (D, C): ([0], [1]),
            (D, D): ([0], [1])
        }

    def reset(self):
        """ Reset instance properties. """
        super().reset()
        self.Rd = create_policy(1, 1, 0, 0)
        self.Rc = {}
        self.Pi = self.Rd   # policy used by MoveGen
        self.violation_counts = {}
        self.v = 0
        self.history_by_cond = {
            (C, C): ([1], [1]),
            (C, D): ([1], [1]),
            (D, C): ([0], [1]),
            (D, D): ([0], [1])
        }
        

    def should_promote(self, r_plus, promotion_threshold=3):
        """
        This function determines if the move r_plus is a deterministic
        behavior of the opponent, and then returns True, or if r_plus 
        is due to a random behavior (or noise) which would require a 
        probabilistic rule, in which case it returns False

        To do so it looks into the game history : if the K last times 
        when the opponent was in the same situation than in r_plus, he 
        played the same thing, then then r_plus is considered as a
        deterministic rule (where K is the user-defined 
        promotion_threshold)

        Parameters

        r_plus : tuple of (tuple of actions.Actions, actions.Actions) 
            exemple: ((C, C), D) 
            r_plus represents one outcome of the history, and the 
            following move played by the opponent
        promotion_threshold : int, optionnal
            number of successive observations needed to promote an
            opponent behavior as a deterministic rule. Default is 3.
        """
        if r_plus[1] == C:
            opposite_action = 0
        elif r_plus[1] == D:
            opposite_action = 1
        k = 1
        count = 0
        # We iterate on the history, while we do not encounter
        # counter-examples of r_plus, i.e. while we do not encounter
        # r_minus
        while(
            k < len(self.history_by_cond[r_plus[0]][0])
            and not (self.history_by_cond[r_plus[0]][0][1:][-k] 
                        == opposite_action
                    and self.history_by_cond[r_plus[0]][1][1:][-k] == 1)
            ):
            # We count every occurrence of r_plus in history
            if (self.history_by_cond[r_plus[0]][1][1:][-k] == 1):
                count += 1
            k += 1
        if (count >= promotion_threshold):
            return True
        return False

    def should_demote(self, r_minus, violation_threshold=4):
        """
        Checks if the number of successive violations of a deterministic
        rule (in the opponent's behavior) exceeds the user-defined 
        violation_threshold
        """
        return (self.violation_counts[r_minus[0]] >= violation_threshold)

    def update_history_by_cond(self, opponent_history):
        """
        Updates self.history_by_cond, between each turns of the game.
        """
        two_moves_ago = (self.history[-2], opponent_history[-2])
        for outcome,GF in self.history_by_cond.items():
            G,F = GF
            if outcome == two_moves_ago:
                if opponent_history[-1] == C:
                    G.append(1)
                else:
                    G.append(0)
                F.append(1)
            else:
                G.append(0)
                F.append(0)

    def compute_prob_rule(self, outcome, alpha=1):
        """
        Uses the game history to compute the probability of the opponent
        playing C, in the outcome situation 
        (exemple : outcome = (C, C)).
        When alpha = 1, the results is approximately equal to the frequency
        of the occurence of outcome -> C. 
        alpha is a discount factor that allows to give more weight to recent
        events than earlier ones.
        
        Parameters

        outcome : tuple of two actions.Actions
            in {(C, C), (C, D), (D, C), (D, D)}
            We want to compute the probability that the opponent plays C
            following this outcome in the game
        alpha : int, optionnal
            Discount factor. Default is 1.
        """
        G = self.history_by_cond[outcome][0]
        F = self.history_by_cond[outcome][1]
        discounted_g = 0
        discounted_f = 0
        alpha_k = 1
        for g,f in zip(G[::-1], F[::-1]):
            discounted_g += alpha_k * g
            discounted_f += alpha_k * f
            alpha_k = alpha * alpha_k
        p_cond = discounted_g / discounted_f
        return p_cond

    def strategy(self, opponent: Player) -> Action:
        # First move
        if not self.history:
            return C
        
        if(len(opponent.history) >= 2):

            # We begin by update history_by_cond
            # (i.e. update Rp)
            self.update_history_by_cond(opponent.history)
     
            two_moves_ago = (self.history[-2], opponent.history[-2])
            # r_plus is the information of what the opponent just played,
            # following the previous outcome two_moves_ago
            r_plus = (two_moves_ago, opponent.history[-1])
            # r_minus is the opposite move, following the same outcome
            r_minus = (two_moves_ago, ({C, D} - {opponent.history[-1]}).pop())

            # If r_plus and r_minus are not in the current set of deterministic 
            # rules, we check if r_plus should be added to it (following the 
            # rule defined in the should_promote function)
            if r_plus[0] not in self.Rc.keys(): 
                if self.should_promote(r_plus, self.promotion_threshold):
                    self.Rc[r_plus[0]] = action_to_int(r_plus[1])
                    self.violation_counts[r_plus[0]] = 0
                    self.violation_counts[r_plus[0]] = 0

            # (if r+ or r- in Rc)
            if r_plus[0] in self.Rc.keys():
                to_check = (C if self.Rc[r_plus[0]] == 1 else D)
                # (if r+ in Rc)
                if r_plus[1] == to_check:
                    # set the violation count of r+ to 0
                    self.violation_counts[r_plus[0]] = 0
                # (if r- in Rc)
                elif r_minus[1] == to_check:
                    # Increment violation count of r-
                    self.violation_counts[r_plus[0]] += 1
                    # As we observe that the behavior of the opponent is
                    # opposed to a rule modeled in Rc, we check if the number
                    # of consecutive violations of this rule is superior to 
                    # a threshold. If it is, we clean Rc, but we keep the rules
                    # of Rc in Rd for smooth transition
                    if self.should_demote(r_minus, self.violation_threshold):
                        self.Rd.update(self.Rc)
                        self.Rc.clear()
                        self.violation_counts.clear()
                        self.v = 0

            # r+ in Rc
            r_plus_in_Rc = (
                r_plus[0] in self.Rc.keys() 
                and self.Rc[r_plus[0]] == action_to_int(r_plus[1])
            )
            # r- in Rd
            r_minus_in_Rd = (
                r_minus[0] in self.Rd.keys()
                and self.Rd[r_minus[0]] == action_to_int(r_minus[1])
            )

            # Increment number of violations of Rd rules
            if r_minus_in_Rd:
                self.v += 1
            # If the number of violations is superior to a threshold, clean Rd
            if (self.v > self.reject_threshold 
                    or (r_plus_in_Rc and r_minus_in_Rd)):
                self.Rd.clear()
                self.v = 0

            # Compute Rp for conditions that are neither in Rc or Rd
            Rp = {}
            all_cond = [(C, C), (C, D), (D, C), (D, D)]
            for outcome in all_cond:
                if ((outcome not in self.Rc.keys()) 
                    and (outcome not in self.Rd.keys())):
                    # Compute opponent's C answer probability
                    Rp[outcome] = self.compute_prob_rule(outcome, self.alpha)

            # We aggregate the rules of Rc, Rd, and Rp in a set of rule Pi
            self.Pi = {}
            # The algorithm makes sure that a rule cannot be in two different
            # sets of rule so we do not need to check for duplicates.
            self.Pi.update(self.Rc)
            self.Pi.update(self.Rd)
            self.Pi.update(Rp)

        # React to the opponent's last move
        return MoveGen((self.history[-1], opponent.history[-1]), self.Pi,
            depth_search_tree=self.tree_depth)


class Node(object):
    """
    Nodes used to build a tree for the tree-search procedure
    The tree has Deterministic ans Stochastic nodes, as the opponent's
    strategy is learned as a probability distribution
    """

    # abstract method
    def get_siblings(self):
        raise NotImplementedError('subclasses must override get_siblings()!')

    # abstract method
    def is_stochastic(self):
        raise NotImplementedError('subclasses must override is_stochastic()!')


class StochasticNode(Node):
    """
    Node that have a probability pC to get to each sibling
    A StochasticNode can be written (C, X) or (D, X), with X = C with
    a probability pC, else X = D
    """

    def __init__(self, own_action, pC, depth):
        self.pC = pC
        self.depth = depth
        self.own_action = own_action

    def get_siblings(self):
        """
        Returns the siblings node of the current StochasticNode
        There are two sibling which are DeterministicNodes, their depth
        is equal to current node depth's + 1 
        This function allows to build the tree
        """
        opponent_c_choice = DeterministicNode(self.own_action, C, self.depth+1)
        opponent_d_choice = DeterministicNode(self.own_action, D, self.depth+1)
        return (opponent_c_choice, opponent_d_choice)

    def is_stochastic(self):
        """
        Returns True if self is a StochasticNode
        """
        return True


class DeterministicNode(Node):
    """
    Nodes (C, C), (C, D), (D, C), or (D, D) with deterministic choice 
    for siblings
    """

    def __init__(self, action1, action2, depth):
        self.action1 = action1
        self.action2 = action2
        self.depth = depth

    def get_siblings(self, policy):
        """
        Returns the siblings node of the current DeterministicNode
        Builds 2 siblings (C, X) and (D, X) that are StochasticNodes
        Those siblings are of the same depth as the current node
        Their probability pC are defined by the policy argument
        """
        c_choice = StochasticNode(
            C, policy[(self.action1, self.action2)], self.depth
            )
        d_choice = StochasticNode(
            D, policy[(self.action1, self.action2)], self.depth
            )
        return (c_choice, d_choice)

    def is_stochastic(self):
        """
        Returns True if self is a StochasticNode
        """
        return False

    def get_value(self):
        values = {
            (C, C): 3,
            (C, D): 0,
            (D, C): 5,
            (D, D): 1
        }
        return values[(self.action1, self.action2)]


def create_policy(pCC, pCD, pDC, pDD):
    """
    Creates a dict that represents a Policy.
    As defined in the reference, a Policy is a set of (prev_move, p) 
    where p is the probability to cooperate after prev_move,
    where prev_move can be (C, C), (C, D), (D, C) or (D, D)

    Parameters

    pCC, pCD, pDC, pDD : float
        Must be between 0 and 1
    """
    return {(C, C): pCC, (C, D): pCD, (D, C): pDC, (D, D): pDD}


def action_to_int(action):
    if action == C:
        return 1
    return 0


def minimax_tree_search(begin_node, policy, max_depth):
    """
    Tree search function (minimax search procedure)
    build by recursion the tree corresponding to a game against 
    opponent's policy, and solve it
    Returns a tuple of two float, that are the utility of playing C,
    and the utility of playing D
    """
    if begin_node.is_stochastic():
        # a stochastic node cannot has the same depth than its parent
        # node hence there is no need to check that his 
        # depth is < max_depth
        siblings = begin_node.get_siblings()
        # The stochastic node value is the expected values of siblings
        node_value = (
            begin_node.pC * minimax_tree_search(
                siblings[0], policy, max_depth) 
            + (1 - begin_node.pC) * minimax_tree_search(
                siblings[1], policy, max_depth)
            )
        return node_value
    else:   # deterministic node
        if begin_node.depth == max_depth:
            # this is an end node, we just return its outcome value
            return begin_node.get_value()
        elif begin_node.depth == 0:
            siblings = begin_node.get_siblings(policy)
            # this returns the two max expected values, for choice C or D,
            # as a tuple
            return (
                minimax_tree_search(siblings[0], policy, max_depth) 
                    + begin_node.get_value(),
                minimax_tree_search(siblings[1], policy, max_depth) 
                    + begin_node.get_value()
                )
        elif begin_node.depth < max_depth:
            siblings = begin_node.get_siblings(policy)
            # the deterministic node value is the max of both siblings values
            # + the score of the outcome of the node
            a = minimax_tree_search(siblings[0], policy, max_depth)
            b = minimax_tree_search(siblings[1], policy, max_depth)
            node_value = max(a, b) + begin_node.get_value()
            return node_value
    

def MoveGen(outcome, policy, depth_search_tree=5):
    """
    Returns the best move considering opponent's policy and last move,
    using tree-search procedure
    """
    current_node = DeterministicNode(outcome[0], outcome[1], depth=0)
    values_of_choices = minimax_tree_search(
            current_node, policy, depth_search_tree)
    # returns the Action which correspond to the best choice in terms of 
    # expected value. In case value(C) == value(D), returns C
    actions_tuple = (C, D)
    return actions_tuple[values_of_choices.index(max(values_of_choices))]
