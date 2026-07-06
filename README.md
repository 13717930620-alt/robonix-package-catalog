# Robonix Package Catalog

This repository indexes Robonix robot deploy repositories and reusable package repositories maintained under the `syswonder` organization.

Naming convention:

- `robot-[company]-[model]`: robot deploy repository. The main artifact is `robonix_manifest.yaml`; deploy-local packages may be included when needed.
- `primitive-[company]-[model]-[primitive_type]-rbnx`: primitive package repository. `primitive_type` follows the Robonix primitive contract namespace, for example `camera`, `lidar`, `imu`, or `chassis`. If the model needs internal word separation, use `_` inside the model segment.
- `service-[service_namespace]-rbnx`: service package repository. The namespace follows `robonix/service/<service_namespace>`.
- `skill-[skill_namespace]-rbnx`: skill package repository. The namespace follows `robonix/skill/<skill_namespace>`.

## Robot Deploys

| Repository | Robot / Deploy | Contract namespace | Notes |
| --- | --- | --- | --- |
| [robot-agilex-rangerminiv3](https://github.com/syswonder/robot-agilex-rangerminiv3) | AgileX Ranger Mini v3 deploy | deploy | Deploy manifest for Ranger Mini v3 with MID-360, RealSense D435i, mapping, navigation, and explore packages. |
| [robot-deeprobotics-lite3](https://github.com/syswonder/robot-deeprobotics-lite3) | DeepRobotics Lite3 deploy | deploy | Repository exists; deploy content is not populated yet. |

## Primitive Packages

| Repository | Hardware | Contract namespace | Notes |
| --- | --- | --- | --- |
| [primitive-agilex-rangerminiv3-chassis-rbnx](https://github.com/syswonder/primitive-agilex-rangerminiv3-chassis-rbnx) | AgileX Ranger Mini v3 chassis | `robonix/primitive/chassis` | CAN/ROS2 wrapper for chassis odometry and velocity command input. |
| [primitive-livox-mid360-lidar-rbnx](https://github.com/syswonder/primitive-livox-mid360-lidar-rbnx) | Livox MID-360 lidar | `robonix/primitive/lidar` | MID-360 PointCloud2 lidar wrapper. |
| [primitive-livox-mid360-imu-rbnx](https://github.com/syswonder/primitive-livox-mid360-imu-rbnx) | Livox MID-360 embedded IMU | `robonix/primitive/imu` | IMU topic shim for the MID-360 IMU stream produced by the lidar driver. |
| [primitive-intel-realsense_d435i-camera-rbnx](https://github.com/syswonder/primitive-intel-realsense_d435i-camera-rbnx) | Intel RealSense D435i camera | `robonix/primitive/camera` | RGB-D camera wrapper with camera streams and snapshot contracts. |

## Service Packages

| Repository | Service | Contract namespace | Notes |
| --- | --- | --- | --- |
| [service-map-rbnx](https://github.com/syswonder/service-map-rbnx) | Mapping / SLAM | `robonix/service/map` | Config-driven mapping service with RTAB-Map-oriented map outputs. |
| [service-navigation-rbnx](https://github.com/syswonder/service-navigation-rbnx) | Navigation | `robonix/service/navigation` | Nav2 wrapper exposing navigation command, status, and cancel contracts. |

## Skill Packages

| Repository | Skill | Contract namespace | Notes |
| --- | --- | --- | --- |
| [skill-explore-rbnx](https://github.com/syswonder/skill-explore-rbnx) | Frontier exploration | `robonix/skill/explore` | Exploration skill using map and navigation service contracts. |

## Notes

- Webots/Tiago examples stay inside the main [`syswonder/robonix`](https://github.com/syswonder/robonix) repository and are not split into standalone package repositories.
- Package repositories should keep their `package_manifest.yaml` capability list aligned with the contracts they actually declare at runtime.
- Robot deploy repositories should reference package URLs under `https://github.com/syswonder/...`.
