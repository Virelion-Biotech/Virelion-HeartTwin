import numpy as np
import pytest

from hearttwin.patient_geometry_audit import audit_surface, exact_surface_candidate, rounded_frame_candidate


def test_exact_coordinate_candidate_closes_index_seam_without_moving_geometry():
    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.], [0., 0., 0.]])
    faces = np.array([[4, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3], [0, 4, 1]])
    original_points, original_faces = points.copy(), faces.copy()
    result = exact_surface_candidate(points, faces)
    assert result["before"]["closed_oriented_nondegenerate"] is False
    assert result["after"]["closed_oriented_nondegenerate"] is True
    assert result["removed_zero_area_face_ids"] == [4]
    assert result["old_to_new_face"][-1] == -1
    assert result["old_to_new_node"] == [0, 1, 2, 3, 0]
    assert result["review_status"] == "pending" and result["solver_ready"] is False
    np.testing.assert_array_equal(points, original_points)
    np.testing.assert_array_equal(faces, original_faces)


def test_candidate_does_not_fill_a_real_hole():
    points = [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
    faces = [[0, 2, 1], [0, 1, 3], [0, 3, 2]]
    result = exact_surface_candidate(points, faces)
    assert result["after"]["closed_oriented_nondegenerate"] is False
    assert result["after"]["edge_incidence_counts"]["1"] == 3
    assert audit_surface(points, faces)["zero_area_triangles"] == 0


def test_bounded_frame_candidate_corrects_rounding_and_preserves_handedness():
    result = rounded_frame_candidate([[1., 0., 0.]], [[1e-5, 1., 0.]], normal=[[0., 0., 1.]], max_angular_correction_rad=2e-5)
    assert result["maximum_abs_dot"] < 1e-14
    assert result["maximum_angular_correction_rad"] < 2e-5
    with pytest.raises(ValueError, match="angular bound"):
        rounded_frame_candidate([[1., 0., 0.]], [[.1, 1., 0.]], max_angular_correction_rad=2e-5)
    left = rounded_frame_candidate([[1., 0., 0.]], [[0., 1., 0.]], normal=[[0., 0., -1.]], max_angular_correction_rad=2e-5)
    assert left["frame_handedness_counts"] == {"right": 0, "left": 1}
    assert left["normal"] == [[0., 0., -1.]]
    with pytest.raises(ValueError, match="normal correction"):
        rounded_frame_candidate([[1., 0., 0.]], [[0., 1., 0.]], normal=[[1., 0., 0.]], max_angular_correction_rad=2e-5)
