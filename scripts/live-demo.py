import os
import sys
import argparse
import ast
import cv2
import time
import torch
from vidgear.gears import CamGear
import numpy as np

sys.path.insert(1, os.getcwd())
from SimpleHRNet import SimpleHRNet
from misc.visualization import draw_points, draw_skeleton, draw_points_and_skeleton, joints_dict, check_video_rotation
from misc.utils import find_person_id_associations
import pickle


def dfs_searcher(tree_root_path, extensions=[".mp4"]):
    extensions = set(extensions)
    path_list = []
    for subpath in os.listdir(tree_root_path):
        abs_path = os.path.abspath(os.path.join(tree_root_path, subpath))
        if os.path.isfile(abs_path):
            _, candidate_ext = os.path.splitext(subpath)
            if candidate_ext in extensions:
                path_list.append(abs_path)
        elif os.path.isdir(abs_path):
            path_list.extend(dfs_searcher(abs_path, extensions))
        else:
            pass
    return path_list


def filter_boxes_pts(boxes, pts, img_height, min_height = 0.1, max_height = 0.8):
    filter1 = np.logical_and((boxes[:, 2] - boxes[:, 0]) > img_height * min_height,
                            (boxes[:, 2] - boxes[:, 0]) < img_height * max_height)
    filter2 = np.sum(pts[:, :, 2], axis=1) > 5
    filter_all = filter1 & filter2
    boxes = boxes[filter_all]
    pts = pts[filter_all]
    cnt = len(pts)
    scores = np.sum(pts[:, :, 2], axis=1)
    idx = np.argsort(scores)[::-1]
    pts = pts[idx]
    boxes = boxes[idx]
    merged_index = set()
    for i in range(cnt):
        if i in merged_index:
            continue
        pnt1 = pts[i][:, :2]
        for j in range(i+1, cnt):
            if j in merged_index:
                continue
            pnt2 = pts[j][:, :2]
            diff = np.abs(pnt1 - pnt2)
            diff = np.sum(diff, axis=1)
            cnt = np.sum(diff < 20)
            if cnt >= 4:
                merged_index.add(j)
                print('found!')

    return boxes, pts

def main(camera_id, filename, hrnet_c, hrnet_j, hrnet_weights, hrnet_joints_set, image_resolution, single_person,
         disable_tracking, max_batch_size, disable_vidgear, save_video, video_format,
         video_framerate, device):
    if device is not None:
        device = torch.device(device)
    else:
        if torch.cuda.is_available() and True:
            torch.backends.cudnn.deterministic = True
            device = torch.device('cuda:0')
        else:
            device = torch.device('cpu')

    print(device)

    image_resolution = ast.literal_eval(image_resolution)
    has_display = 'DISPLAY' in os.environ.keys() or sys.platform == 'win32'
    video_writer = None
    model = SimpleHRNet(
        hrnet_c,
        hrnet_j,
        hrnet_weights,
        resolution=image_resolution,
        multiperson=not single_person,
        return_bounding_boxes=not disable_tracking,
        max_batch_size=max_batch_size,
        device=device
    )

    videos = dfs_searcher('/home/shiyong/Cortica/Research/Juno/Videos')
    out_names = ["_".join(fn.split(os.path.sep)[1:]) for fn in videos]
    print(len(out_names))
    has_display = False
    save_video = False
    video_height = 1440
    out_video_folder = '/home/shiyong/Cortica/Research/Juno/Videos/all_out_no_pose_resize_800'
    os.makedirs(out_video_folder, exist_ok=True)
    for idx, (filename, out_name) in enumerate(zip(videos, out_names)):
        print(idx)
        data_name = os.path.splitext(os.path.basename(out_name))[0] + '.pkl'
        out_data = []
        if filename is not None:
            rotation_code = None #check_video_rotation(filename)
            video = cv2.VideoCapture(filename)
            assert video.isOpened()
        else:
            rotation_code = None
            if disable_vidgear:
                video = cv2.VideoCapture(camera_id)
                assert video.isOpened()
            else:
                video = CamGear(camera_id).start()

        if not disable_tracking:
            prev_boxes = None
            prev_pts = None
            prev_person_ids = None
            next_person_id = 0

        while True:
            t = time.time()

            if filename is not None or disable_vidgear:
                ret, frame = video.read()
                if not ret:
                    break
                if rotation_code is not None:
                    frame = cv2.rotate(frame, rotation_code)
            else:
                frame = video.read()
                if frame is None:
                    break

            pts = model.predict(frame)

            if not disable_tracking:
                boxes, pts = pts
                #boxes, pts = filter_boxes_pts(boxes, pts, video_height)

            if not disable_tracking:
                if len(pts) > 0:
                    if prev_pts is None and prev_person_ids is None:
                        person_ids = np.arange(next_person_id, len(pts) + next_person_id, dtype=np.int32)
                        next_person_id = len(pts) + 1
                    else:
                        boxes, pts, person_ids = find_person_id_associations(
                            boxes=boxes, pts=pts, prev_boxes=prev_boxes, prev_pts=prev_pts, prev_person_ids=prev_person_ids,
                            next_person_id=next_person_id, pose_alpha=0.0, similarity_threshold=0.4, smoothing_alpha=0.1,
                        )
                        next_person_id = max(next_person_id, np.max(person_ids) + 1)
                else:
                    person_ids = np.array((), dtype=np.int32)

                prev_boxes = boxes.copy()
                prev_pts = pts.copy()
                prev_person_ids = person_ids

            else:
                person_ids = np.arange(len(pts), dtype=np.int32)

            out_data.append((pts, person_ids, boxes))
            if save_video:
                for box in boxes:
                    x1, y1, x2, y2 = box
                    frame = cv2.line(frame, (x1, y1), (x1, y2), color=(255, 0, 0), thickness=2)
                    frame = cv2.line(frame, (x1, y2), (x2, y2), color=(255, 0, 0), thickness=2)
                    frame = cv2.line(frame, (x2, y2), (x2, y1), color=(255, 0, 0), thickness=2)
                    frame = cv2.line(frame, (x2, y1), (x1, y1), color=(255, 0, 0), thickness=2)
                for i, (pt, pid) in enumerate(zip(pts, person_ids)):
                    frame = draw_points_and_skeleton(frame, pt, joints_dict()[hrnet_joints_set]['skeleton'], person_index=pid,
                                                     points_color_palette='gist_rainbow', skeleton_color_palette='jet',
                                                     points_palette_samples=10)

            fps = 1. / (time.time() - t)
            print('\rframerate: %f fps' % fps, end='')

            if has_display:
                cv2.imshow('frame.png', frame)
                k = cv2.waitKey(1)
                if k == 27:  # Esc button
                    if disable_vidgear:
                        video.release()
                    else:
                        video.stop()
                    break
            # else:
            #     cv2.imwrite('frame.png', frame)

            if save_video:
                if video_writer is None:
                    fourcc = cv2.VideoWriter_fourcc(*video_format)  # video format
                    video_writer = cv2.VideoWriter(os.path.join(out_video_folder, out_name), fourcc, video_framerate, (frame.shape[1], frame.shape[0]))
                video_writer.write(frame)

        if save_video:
            video_writer.release()
            video_writer = None
        with open(os.path.join(out_video_folder, data_name), 'wb') as fp:
            pickle.dump(out_data, fp)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera_id", "-d", help="open the camera with the specified id", type=int, default=0)
    parser.add_argument("--filename", "-f", help="open the specified video (overrides the --camera_id option)",
                        type=str, default=None)
    parser.add_argument("--hrnet_c", "-c", help="hrnet parameters - number of channels", type=int, default=48)
    parser.add_argument("--hrnet_j", "-j", help="hrnet parameters - number of joints", type=int, default=17)
    parser.add_argument("--hrnet_weights", "-w", help="hrnet parameters - path to the pretrained weights",
                        type=str, default="../weights/pose_hrnet_w48_384x288.pth")
    parser.add_argument("--hrnet_joints_set",
                        help="use the specified set of joints ('coco' and 'mpii' are currently supported)",
                        type=str, default="coco")
    parser.add_argument("--image_resolution", "-r", help="image resolution", type=str, default='(384, 288)')
    parser.add_argument("--single_person",
                        help="disable the multiperson detection (YOLOv3 or an equivalen detector is required for"
                             "multiperson detection)",
                        action="store_true")
    parser.add_argument("--disable_tracking",
                        help="disable the skeleton tracking and temporal smoothing functionality",
                        action="store_true")
    parser.add_argument("--max_batch_size", help="maximum batch size used for inference", type=int, default=16)
    parser.add_argument("--disable_vidgear",
                        help="disable vidgear (which is used for slightly better realtime performance)",
                        action="store_true")  # see https://pypi.org/project/vidgear/
    parser.add_argument("--save_video", help="save output frames into a video.", action="store_false")
    parser.add_argument("--video_format", help="fourcc video format. Common formats: `MJPG`, `XVID`, `X264`."
                                                     "See http://www.fourcc.org/codecs.php", type=str, default='MJPG')
    parser.add_argument("--video_framerate", help="video framerate", type=float, default=30)
    parser.add_argument("--device", help="device to be used (default: cuda, if available)", type=str, default=None)
    args = parser.parse_args()
    main(**args.__dict__)
