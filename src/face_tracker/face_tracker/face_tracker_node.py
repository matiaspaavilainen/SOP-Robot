import cProfile
import io
import pstats
import rclpy
import cv2
import dlib
import os
import numpy as np
import time
import sys
import traceback
from typing import List
from deepface import DeepFace

from ament_index_python.packages import get_package_share_directory

from rclpy.node import Node

from std_msgs.msg import String
from sensor_msgs.msg import Image
# import face_tracker_msgs.msg as msg
from face_tracker_msgs.msg import Faces, Face as FaceMsg, Point2

from cv_bridge import CvBridge, CvBridgeError

from .lip_movement_net import LipMovementDetector
from .face_recognition import FaceRecognizer
from .face import Face

bridge = CvBridge()

class WebcamError(Exception):
    """signal that webcam has stopped working"""
    pass

#pr = cProfile.Profile()

# TODO: Ask from Aapo, where to store the database
DEFAULT_FACE_DB_PATH = os.path.expanduser('~')+"/database"

class FaceTracker(Node):
    def __init__(self, lip_movement_detection=True, face_recognizer=True, face_db_path=DEFAULT_FACE_DB_PATH):
        super().__init__("face_tracker")
        self.lip_movement_detection = lip_movement_detection
        self.logger = self.get_logger()

        face_image_topic = (
            self.declare_parameter(
                "face_image_topic", "image_face"
            )  # non-absolute paths are inside the current node namespace
            .get_parameter_value()
            .string_value
        )
        face_topic = (
            self.declare_parameter(
                "face_topic", "faces"
            )  # non-absolute paths are inside the current node namespace
            .get_parameter_value()
            .string_value
        )
        predictor = (
            self.declare_parameter("predictor", "shape_predictor_68_face_landmarks.dat")
            .get_parameter_value()
            .string_value
        )

        self.face_detector = dlib.get_frontal_face_detector()
        self.predictor = dlib.shape_predictor(
            os.path.join(
                get_package_share_directory("face_tracker"),
                "predictors",
                predictor,
            )
        )
        if self.lip_movement_detection:
            lip_movement_detector = (
                self.declare_parameter("lip_movement_detector", "1_32_False_True_0.25_lip_motion_net_model.h5")
                .get_parameter_value()
                .string_value
            )
           # Initialize lip movement detector
            self.logger.info('Initializing lip movement detector...')
            self.lip_movement_detector = LipMovementDetector(
                os.path.join(
                    get_package_share_directory("face_tracker"),
                    "models",
                    lip_movement_detector,
                ),
                self.predictor
            )
            self.logger.info('Lip movement detector initialized.')
        else:
            self.logger.info('Lip movement detection disabled.')

        # Face recognition
        if face_recognizer:
            self.face_recognizer = FaceRecognizer(db_path=face_db_path,
                                                  logger=self.logger,
                                                  model_name="SFace",
                                                  detector_backend="yunet",
                                                  distance_metric="cosine") # uses our own implemenation
        else:
            self.face_recognizer = None

        # TODO: implement proper way to save faces
        self.face_ids, self.face_representations = self.face_recognizer.get_database_representations()

        self.face_img_publisher = self.create_publisher(Image, face_image_topic, 5)
        self.face_publisher = self.create_publisher(Faces, "face_topic", 1)
        self.face_location_publisher = self.create_publisher(Point2, 'face_location_topic', 1)

        self.frame = 0
        self.faces: List[Face] = []

        self.cap = None
        self.font = cv2.FONT_HERSHEY_SIMPLEX

        # Run face tracker
        self.webcam_loop()
        
        #self.timer = self.create_timer(2, self.profile_cycle)
        #pr.enable()

    """def profile_cycle(self):
        global pr
        pr.disable()
        s = io.StringIO()
        ps = pstats.Stats(pr, stream=s).sort_stats(pstats.SortKey.CUMULATIVE)
        ps.print_stats()
        self.logger.info("Profiler: -----------------------------")
        self.logger.info(s.getvalue())
        self.logger.info("Profiler: -----------------------------")

        pr = cProfile.Profile()
        pr.enable()"""
    
    def webcam_loop(self):
        fps = FramesPerSecond()
        fps.start()

        self.open_webcam()
        while True:
            # Read a frame from the video stream
            try:
                # Capture a frame from webcam
                ret, frame = self.cap.read()
                if not ret:
                    raise WebcamError

            except WebcamError:
                self.logger.error("[*] something went wrong, restarting webcam..")
                # close and try reopening webcam 
                self.close_webcam()
                self.open_webcam()

            try:
                # Process the frame
                self.on_frame_received(frame=frame)
            except Exception as e:
                self.logger.error(traceback.format_exc())

            # Draw fps to the frame
            cv2.putText(frame,
                        '%.2f' % fps.fps,
                        (10, 20),
                        self.font,
                        0.5,
                        (255, 255, 255),
                        1,
                        cv2.LINE_AA) 

            try:
                # Publish modified frame image
                self.face_img_publisher.publish(bridge.cv2_to_imgmsg(frame, "bgr8"))
            except CvBridgeError as e:
                self.logger.warn("Could not convert ros img to opencv image: ", e)

            fps.update_fps()

        # TODO: Close webcam properly
        # self.close_webcam()
            
    def on_frame_received(self, frame: cv2.typing.MatLike):
        cv2_gray_img = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        msg_faces = []

        # Get the face locations
        if self.frame == 0:
            faces_len_old = len(self.faces)
            # Use face detection to get face locations
            self.faces = self.analyze_frame(frame)

            # Initialize new input sequences for lip movement detector if the number of detected faces change
            if self.lip_movement_detection:
                if faces_len_old != len(self.faces):
                    #TODO: original implementation had speaking state clearing here
                    self.lip_movement_detector.initialize_input_sequence(len(self.faces))

            # self.logger.info(f"Face detection: faces={len(self.faces)}")
            
        else:
            # Use dlib correlation tracker to update face locations
            for face in self.faces:
                face.update_location(frame)

            # self.logger.info(f"correlation tracking: faces={len(self.faces)}")
        
        # loop through all faces
        for i, face in enumerate(self.faces):
            if self.lip_movement_detection:
                # Determine if the face is speaking or silent
                face.speaking = self.lip_movement_detector.test_video_frame(cv2_gray_img, face.rect, i)

            # # Run face recognition
            # if self.face_recognizer:

            #     if self.frame == 0:
            #         # self.logger.info(f"face recognition: face_index={i}")
            #         identity = self.face_recognizer.find(face.image)
            #         face.update_identity(identity)
            
            # Draw information to frame
            self.draw_face_info(frame, face, self.font)

            msg_face = FaceMsg(top_left=Point2(x=face.left, y=face.top), bottom_right=Point2(x=face.right, y=face.bottom))
            msg_faces.append(msg_face)

        self.frame += 1
        # Set frame to zero for new detection every nth frame.
        # Large values lead to drifting of the detected faces
        n = 5
        self.frame = self.frame % n

        # publish faces
        # self.publish_face_location() largest face calculating not implemented yet
        self.face_publisher.publish(Faces(faces=msg_faces))
    
    def analyze_frame(self, frame):
        """
        Get face objects from frame. Detect faces and recognize them. Intialize dlib correlation trackers.
        """
        faces: List[Face] = []

        # Uses deepface to extract face locations from frame
        face_objs = self.face_recognizer.extract_faces(frame)

        # self.logger.info(f"face_objs: {face_objs}")
        
        for face_obj in face_objs:
            
            face_img = face_obj["face"]
            face_region = face_obj["facial_area"]
            x = face_region["x"]
            y = face_region["y"]
            w = face_region["w"]
            h = face_region["h"]

            face: Face = None
            representation: List[float] = self.face_recognizer.represent(face_img)

            # Compare face to the database
            if self.face_recognizer:
                matching_index, distance = self.face_recognizer.match_face(representation, self.face_representations)
                if matching_index is not None:
                    identity = self.face_ids[matching_index]
                else:
                    identity = None
            
            # Compare face to previously found faces using distance between them
            if len(self.faces) != 0:

                matched_face: Face = self.find_matching_face((x, y, w, h), representation, self.faces)

                if matched_face is not None:
                    matched_face.update(x, x + w, y, y + h, face_img, representation)
                    face = matched_face

                    self.logger.info("Same face found")

            if face is None:
                # Matching face not found, create new one
                face = Face(x, x + w, y, y + h, face_img, representation)

                self.logger.info("new face found")
            if identity is not None:
                face.update_identity(identity, distance)
            face.start_track(frame)
            faces.append(face)
        return faces

    def find_matching_face(self, face_coords, representation, faces, distance_treshold=30):
        """
        Method for finding maching face in faces list.

        face_coords Tuple(x, y, w, h)

        representation (List[float]): Multidimensional vector representing facial features.
            The number of dimensions varies based on the reference model

        faces List[Face]: List of Face object, where matching face are looked from.

        return: maching Face object or None
        """
        # First compare representations
        representations = [face.representation for face in faces]
        matching_index, distance = self.face_recognizer.match_face(representation, representations)
        if matching_index is not None:
            return faces[matching_index]
        
        (x, y, w, h) = face_coords

        # Find closes face
        closest_face, distance = self.closest_face(x, y, w, h, faces)
        if distance < distance_treshold:
            return closest_face
        
        return None

    @staticmethod
    def closest_face(x, y, w, h, faces):
        """
        Method to find closes face from list of faces.
        Returns: tuple (closes face, distance) or None
        """
        if not faces:
            return None

        closest_face: Face = None
        distance = None

        #middle point
        middle_point = np.array([x + w / 2, y + h / 2])

        closest_face: Face = None
        min_distance = None

        for face in faces:
            # TODO: use deepface to verify that faces are same?
            # Calculate distance to face
            face_middle_point = np.array([face.left + (face.right - face.left) / 2,
                                          face.top + (face.bottom - face.top) / 2])
            distance = np.linalg.norm(middle_point-face_middle_point)
            if not min_distance or distance < min_distance:
                min_distance = distance
                closest_face = face

        return closest_face, min_distance

    @staticmethod
    def _crop_face_image(original_img, face_coords, padding=None, resize=True):
        '''
        Crop face image from original_img using face_coords.
        if padding is not None, add padding to face coordinates to include more of the detected face
        for better emotion recognition.
        If no room for padding, return the original sized face image.
        :return: 48x48 image or None
        '''
        # unpack face coordinates from a tuple
        x, y, w, h = face_coords

        if padding is not None and padding > 0:
            # get array/image shape
            y_max, x_max, *_ = original_img.shape

            # check if padded coordinates are within bounds
            if (0 <= y-padding and y+h+padding < y_max and
            0 <= x-padding and x+w+padding < x_max): 
                x -= padding
                y -= padding
                h += padding
                w += padding

        # crop face from the webcam image
        face = original_img[y:y+h, x:x+w]

        if resize:
            face = cv2.resize(face, (124, 124), interpolation=cv2.INTER_NEAREST)
        # return face
        return face

    
    @staticmethod
    def draw_face_info(frame, face:Face, font):
        """
        Draws rectangle around face and other information to display 
        """
        green = (0, 255, 0)

        # Draw rectangle around the face
        cv2.rectangle(frame, (face.left, face.top), (face.right, face.bottom), green, 1)

        if face.speaking is not None:
            cv2.putText(frame,
                        face.speaking,
                        (face.left + 2, face.bottom + 10 - 3),
                        font,
                        0.3,
                        (255, 255, 255),
                        1,
                        cv2.LINE_AA)
        

        if face.identity:
            cv2.putText(frame,
                        f"Identity: {face.identity}",
                        (face.left + 2, face.top + 10),
                        font,
                        0.3,
                        (255, 255, 255),
                        1,
                        cv2.LINE_AA)  
               
            cv2.putText(frame,
                        f"Last result: {face.last_identity}",
                        (face.left + 2, face.top + 20),
                        font,
                        0.3,
                        (255, 255, 255),
                        1,
                        cv2.LINE_AA)     

    def publish_face_location(self):
        # Check that there is a location to publish
        if self.face_location:
            # Publish face location
            self.face_location_publisher.publish(self.face_location)
            # Set location back to None to prevent publishing same location multiple times
            self.face_location = None

    def open_webcam(self):
        '''
        Open webcam handle
        '''
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            self.logger.fatal("[*] Cannot open a webcam!")
            sys.exit(1)


    def close_webcam(self):
        '''
        Destroy webcam handle and close all windows
        '''
        self.logger.info("closing webcam handle...")
        self.cap.release()
        cv2.destroyAllWindows()

class FramesPerSecond:
    """
    Class for calculating real time fps of video stream. Code is based from stack owerflow thread:
    https://stackoverflow.com/questions/55154753/trouble-calculating-fps-on-output-video-stream
    """
    def __init__(self):
        self.startTime = None
        self.total_number_of_frames = 0
        self.counter = 0
        self.frameRate = 1  # The number of seconds to wait for each measurement.
        self.fps = 0

    def start(self):
        self.startTime = time.time()  # Returns a UNIX timestamp.

    def update_fps(self):
        self.total_number_of_frames += 1
        self.counter += 1  # Count will increase until the if condition executes.
        if self._elapsed_time() > self.frameRate:  # We measure the self only after 1 second has passed.
            self.fps = self.counter / self._elapsed_time()
            self.counter = 0  # reset the counter for next iteration.
            self.start()  # reset the start time.

    def _elapsed_time(self):
        return time.time() - self.startTime

def main(args=None):
    # Initialize
    rclpy.init(args=args)
    tracker = FaceTracker(lip_movement_detection=True, face_recognizer=True)

    # Do work
    rclpy.spin(tracker)

    # Shutdown
    tracker.destroy_node()
    rclpy.shutdown()

if __name__ == "__main__":
    main()
