"""Multi-agent environments for scenario with grid and AVs

These environments are used to train AVs to regulate traffic flow through an
n x m grid.
"""

from copy import deepcopy

import numpy as np
from gym.spaces.box import Box

from flow.core import rewards
from flow.envs.green_wave_env import PO_TrafficLightGridEnv
from flow.multiagent_envs.multiagent_env import MultiEnv

MAX_LANES = 1

ADDITIONAL_ENV_PARAMS = {
    # maximum acceleration for autonomous vehicles, in m/s^2
    "max_accel": 3,
    # maximum deceleration for autonomous vehicles, in m/s^2
    "max_decel": 3,
}

# Keys for RL experiments
ADDITIONAL_RL_ENV_PARAMS = {
    # velocity to use in reward functions
    "target_velocity": 30,
    # if an RL vehicle exits, place it back at the front
    "add_rl_if_exit": True,
}


class MultiGridAVsPOEnv(PO_TrafficLightGridEnv, MultiEnv):
    """Multiagent shared model version of PO_TrafficLightGridEnv.

    Required from env_params: See parent class

    States
        See parent class
    Actions
        See parent class
    Rewards
        See parent class
    Termination
        See parent class

    """

    def __init__(self, env_params, sim_params, scenario, simulator='traci'):
        super().__init__(env_params, sim_params, scenario, simulator)
        for p in ADDITIONAL_RL_ENV_PARAMS.keys():
            if p not in env_params.additional_params:
                raise KeyError(
                    'Environment parameter "{}" not supplied'.format(p))

        for p in ADDITIONAL_ENV_PARAMS.keys():
            if p not in env_params.additional_params:
                raise KeyError(
                    'Environment parameter "{}" not supplied'.format(p))

        # number of nearest lights to observe, defaults to 4
        self.num_local_lights = 4

        # number of nearest edges to observe, defaults to 4
        self.num_local_edges = 4

        # number of nearest edges to observe, defaults to 4
        self.traffic_lights = self.net_params.additional_params.get(
            "traffic_lights", False)

        self.add_rl_if_exit = env_params.get_additional_param("add_rl_if_exit")
        self.num_rl = deepcopy(self.initial_vehicles.num_rl_vehicles)
        self.rl_id_list = deepcopy(self.initial_vehicles.get_rl_ids())
        self.max_speed = self.k.scenario.max_speed()

        # list of controlled edges for comparison
        outer_edges = []
        outer_edges += ["left{}_{}".format(self.rows, i) for i in range(
            self.cols)]
        # outer_edges += ["right0_{}".format(i) for i in range(N_ROWS)]
        outer_edges += ["bot{}_0".format(i) for i in range(self.rows)]
        self.controlled_edges = outer_edges

    @property
    def observation_space(self):
        """State space that is partially observed.

        Velocities, distance to intersections, RL or not (for nearby
        vehicles) from each direction, local edge information, and traffic
        light state.
        """
        traffic_light_obs = 3 * (1 + self.num_local_lights) * \
                            self.traffic_lights
        # TODO(cathywu) CHANGE
        tl_box = Box(
            low=0.,
            high=1,
            shape=(3 * 4 * self.num_observed +
                   2 * self.num_local_edges +
                   traffic_light_obs,
                   ),
            dtype=np.float32)
        return tl_box

    @property
    def action_space(self):
        """See class definition."""
        add_params = self.env_params.additional_params
        max_accel = add_params.get("max_accel")
        max_decel = add_params.get("max_decel")
        # TODO(cathywu) later on, support num_lanes
        # num_lanes = self.k.scenario.num_lanes()
        return Box(
            low=-max_decel*self.sim_step, high=max_accel*self.sim_step,
            shape=(1, ), dtype=np.float32)

    def get_state(self):
        """Observations for each traffic light agent.

        :return: dictionary which contains agent-wise observations as follows:
        - For the self.num_observed number of vehicles closest and incoming
        towards traffic light agent, gives the vehicle velocity, distance to
        intersection, edge_number, density traffic light state.
        - For edges in the network, gives the density and average velocity.
        - For the self.num_local_lights number of nearest lights (itself
        included), gives the traffic light information, including the last
        change time, light direction (i.e. phase), and a currently_yellow flag.
        """
        # TODO(cathywu) CHANGE
        # Normalization factors
        max_speed = max(
            self.k.scenario.speed_limit(edge)
            for edge in self.k.scenario.get_edge_list())
        grid_array = self.net_params.additional_params["grid_array"]
        max_dist = max(grid_array["short_length"], grid_array["long_length"],
                       grid_array["inner_length"])

        # TODO(cathywu) refactor PO_TrafficLightGridEnv with convenience
        # methods for observations, but remember to flatten for single-agent

        # Observed vehicle information
        speeds = []
        dist_to_intersec = []
        veh_types = []
        # edge_number = []
        all_observed_ids = []
        for _, edges in self.scenario.node_mapping:
            local_speeds = []
            local_dists_to_intersec = []
            local_veh_types = []
            # local_edge_numbers = []
            for edge in edges:
                observed_ids = \
                    self.k_closest_to_intersection(edge, self.num_observed)
                all_observed_ids.append(observed_ids)

                # check which edges we have so we can always pad in the right
                # positions
                local_speeds.extend(
                    [self.k.vehicle.get_speed(veh_id) / max_speed for veh_id in
                     observed_ids])
                local_dists_to_intersec.extend([(self.k.scenario.edge_length(
                    self.k.vehicle.get_edge(
                        veh_id)) - self.k.vehicle.get_position(
                    veh_id)) / max_dist for veh_id in observed_ids])
                local_veh_types.extend(
                    [1 if veh_id in self.k.vehicle.get_rl_ids() else 0 for
                     veh_id in observed_ids])
                # local_edge_numbers.extend([self._convert_edge(
                #     self.k.vehicle.get_edge(veh_id)) / (
                #                                self.k.scenario.network.num_edges - 1) for veh_id in
                #                            observed_ids])

                if len(observed_ids) < self.num_observed:
                    diff = self.num_observed - len(observed_ids)
                    local_speeds.extend([0] * diff)
                    local_dists_to_intersec.extend([0] * diff)
                    local_veh_types.extend([0] * diff)
                    # local_edge_numbers.extend([0] * diff)

            speeds.append(local_speeds)
            dist_to_intersec.append(local_dists_to_intersec)
            veh_types.append(local_veh_types)
            # edge_number.append(local_edge_numbers)

        # Edge information
        density = []
        velocity_avg = []
        for edge in self.k.scenario.get_edge_list():
            ids = self.k.vehicle.get_ids_by_edge(edge)
            if len(ids) > 0:
                # TODO(cathywu) Why is there a 5 here?
                density += [5 * len(ids) / self.k.scenario.edge_length(edge)]
                velocity_avg += [np.mean(
                    [self.k.vehicle.get_speed(veh_id) for veh_id in
                     ids]) / max_speed]
            else:
                density += [0]
                velocity_avg += [0]
        density = np.array(density)
        velocity_avg = np.array(velocity_avg)
        self.observed_ids = all_observed_ids

        if self.traffic_lights:
            # Traffic light information
            last_change = self.last_change.flatten()
            direction = self.direction.flatten()
            currently_yellow = self.currently_yellow.flatten()
            # Dummy values for lights that are out of range
            # TODO(cathywu) are these values reasonable?
            last_change = np.append(last_change, [0])
            direction = np.append(direction, [0])
            currently_yellow = np.append(currently_yellow, [0])

        obs = {}
        # TODO(cathywu) allow differentiation between rl and non-rl lights
        node_to_edges = self.scenario.node_mapping
        for rl_id in self.k.traffic_light.get_ids():
            rl_id_num = int(rl_id.split("center")[1][0])
            local_edges = node_to_edges[rl_id_num][1]
            local_edge_numbers = [self.k.scenario.get_edge_list().index(e)
                                  for e in local_edges]
            local_id_nums = [rl_id_num, self._get_relative_node(rl_id, "top"),
                             self._get_relative_node(rl_id, "bottom"),
                             self._get_relative_node(rl_id, "left"),
                             self._get_relative_node(rl_id, "right")]

            if self.traffic_lights:
                observation = np.array(np.concatenate(
                    [speeds[rl_id_num], dist_to_intersec[rl_id_num],
                     veh_types[rl_id_num], density[local_edge_numbers],
                     velocity_avg[local_edge_numbers],
                     last_change[local_id_nums], direction[local_id_nums],
                     currently_yellow[local_id_nums]]))
            else:
                observation = np.array(np.concatenate(
                    [speeds[rl_id_num], dist_to_intersec[rl_id_num],
                     veh_types[rl_id_num], density[local_edge_numbers],
                     velocity_avg[local_edge_numbers], ]))
            obs.update({rl_id: observation})

        return obs

    def _apply_rl_actions(self, rl_actions):
        """
        See parent class.

        Issues new target speed for each AV agent.
        """
        for rl_id, rl_action in rl_actions.items():
            edge = self.k.vehicle.get_edge(rl_id)
            if edge:
                # If in outer lanes, on a controlled edge, in a controlled lane
                if edge[0] != ':' and edge in self.controlled_edges:
                    max_speed_curr = self.k.vehicle.get_max_speed(rl_id)
                    next_max = np.clip(max_speed_curr + rl_action, 0.01, 23.0)
                    self.k.vehicle.set_max_speed(rl_id, next_max)

                else:
                    # set the desired velocity of the controller to the default
                    self.k.vehicle.set_max_speed(rl_id, 23.0)

    def compute_reward(self, rl_actions, **kwargs):
        """See class definition."""
        if rl_actions is None:
            return {}

        if self.env_params.evaluate:
            rew = -rewards.min_delay_unscaled(self)
        else:
            rew = -rewards.min_delay_unscaled(self) \
                  + rewards.penalize_standstill(self, gain=0.2)

        # each agent receives reward normalized by number of lights
        rew /= self.num_traffic_lights

        rews = {}
        for rl_id in rl_actions.keys():
            rews[rl_id] = rew
        return rews

    def additional_command(self):
        """Reintroduce any RL vehicle that may have exited in the last step.

        This is used to maintain a constant number of RL vehicle in the system
        at all times, in order to comply with a fixed size observation and
        action space.
        """
        """See class definition."""
        super().additional_command()
        # if the number of rl vehicles has decreased introduce it back in
        num_rl = self.k.vehicle.num_rl_vehicles
        if num_rl != len(self.rl_id_list) and self.add_rl_if_exit:
            # find the vehicles that have exited
            diff_list = list(
                set(self.rl_id_list).difference(self.k.vehicle.get_rl_ids()))
            for rl_id in diff_list:
                # distribute rl cars evenly over lanes
                lane_num = self.rl_id_list.index(rl_id) % \
                           MAX_LANES * self.scaling
                # reintroduce it at the start of the network
                try:
                    self.k.vehicle.add(
                        veh_id=rl_id,
                        edge='1',
                        type_id=str('rl'),
                        lane=str(lane_num),
                        pos="0",
                        speed="max")
                except Exception:
                    pass

        # specify observed vehicles
        for veh_ids in self.observed_ids:
            for veh_id in veh_ids:
                self.k.vehicle.set_observed(veh_id)