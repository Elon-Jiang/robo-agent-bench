"""Negative controls for the righting reward (no robot motion required)."""

import unittest

import numpy as np
import torch

from rab.tasks import right_mug


class RightMugRewardTest(unittest.TestCase):

    def features(self, angle=0, robot_contacts=0, table_contacts=1, speed=0):
        angle = np.radians(angle)
        return {
            "mug/xquat": torch.tensor([[np.cos(angle / 2), np.sin(angle / 2), 0, 0]]),
            "mug/joint_vel": torch.tensor(
                [[speed, 0, 0, 0, 0, 0]], dtype=torch.float32
            ),
            "righting_robot_contact/found": torch.tensor([[[robot_contacts]]]),
            "righting_table_contact/found": torch.tensor([[[table_contacts]]]),
        }

    def check(self, **kwargs):
        return right_mug.check_righted(
            self.features(**kwargs), gripper_range=(0, 1), released_norm=0.35
        ).item()

    def test_upright_tolerance_and_inversion(self):
        self.assertTrue(self.check(angle=0))
        self.assertTrue(self.check(angle=14.9))
        for angle in (15.1, 90, 180):
            self.assertFalse(self.check(angle=angle))

    def test_held_mug_is_not_released(self):
        self.assertFalse(self.check(robot_contacts=1))

    def test_airborne_mug_is_not_standing(self):
        self.assertFalse(self.check(table_contacts=0))

    def test_moving_mug_is_not_settled(self):
        self.assertFalse(self.check(speed=0.1))

    def test_batched_reward(self):
        upright = self.features()
        sideways = self.features(angle=90)
        features = {
            key: torch.cat([value, sideways[key]]) for key, value in upright.items()
        }
        result = right_mug.check_righted(
            features, gripper_range=(0, 1), released_norm=0.35
        )
        self.assertEqual(result.tolist(), [True, False])
