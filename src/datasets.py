import io
import json
import os
import tarfile
import urllib.request
from urllib.error import HTTPError

import tensorflow as tf
from preprocess import Tokenizer
from tqdm import tqdm


class PersonalityCaptions:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.image_dir = os.path.join(self.data_dir, "images")
        self.image_url_prefix = "https://multimedia-commons.s3-us-west-2.amazonaws.com/data/images"
        self.captions_url = "http://parl.ai/downloads/personality_captions/personality_captions.tgz"
        self.train_file, self.val_file, self.test_file = "train.json", "val.json", "test.json"
        self.dataset_files = {"train": self.train_file, "val": self.val_file, "test": self.test_file}
        self.metadata_files = ["personalities.json", "personalities.txt"]

    def download(self):
        os.makedirs(self.data_dir, exist_ok=True)
        if not all([cf in os.listdir(self.data_dir) for cf in self.metadata_files + list(self.dataset_files.values())]):
            response = urllib.request.urlopen(self.captions_url)
            tar = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
            tar.extractall(path=self.data_dir)
            tar.close()
        hashes = []
        for fname in self.dataset_files.values():
            with open(os.path.join(self.data_dir, fname), "r") as f:
                data = json.load(f)
                hashes += [d["image_hash"] for d in data]
        os.makedirs(self.image_dir, exist_ok=True)
        downloaded_images = set(os.listdir(self.image_dir))
        for hash in tqdm(hashes, unit="img"):
            image_fname = f"{hash}.jpg"
            if image_fname in downloaded_images:
                continue
            image_url = f"{self.image_url_prefix}/{hash[:3]}/{hash[3:6]}/{image_fname}"
            try:
                response = urllib.request.urlopen(image_url)
                with open(os.path.join(self.image_dir, image_fname), "wb") as f:
                    f.write(response.read())
                    downloaded_images.add(image_fname)
            except HTTPError as e:
                print(f"HTTP Error {e.code} - {image_url}")
                continue

    def load(self, split):
        file_to_load = self.dataset_files[split]
        downloaded_images = set(os.listdir(self.image_dir))
        with open(os.path.join(self.data_dir, file_to_load), "r") as f:
            data = json.load(f)
        data = filter(lambda d: f"{d['image_hash']}.jpg" in downloaded_images, data)
        data = map(lambda d: {
            "style": d["personality"],
            "caption": d["comment"],
            "additional_captions": d["additional_comments"] if "additional_comments" in d else [],
            "image_path": os.path.join(self.image_dir, f"{d['image_hash']}.jpg")
        }, data)
        return list(data)


class DatasetLoader:
    def __init__(self, dataset):
        self.dataset = dataset
        self.tokenizer = Tokenizer()
        self.tokenizer.fit_on_texts([d["caption"] for d in self.dataset.load("train")])

    def load(self, split, batch_size):
        data = self.dataset.load(split)
        image_paths = tf.convert_to_tensor([d["image_path"] for d in data])
        sequences = tf.ragged.constant([self.tokenizer.texts_to_sequences([d["caption"]])[0] for d in data])
        sequence_lengths = tf.convert_to_tensor([t.shape[0] for t in sequences])

        tf_dataset = tf.data.Dataset.from_tensor_slices((image_paths, sequences, sequence_lengths))
        tf_dataset = tf_dataset.map(self._dataset_mapper, num_parallel_calls=tf.data.experimental.AUTOTUNE)
        tf_dataset = tf_dataset.shuffle(buffer_size=1000, reshuffle_each_iteration=True)
        tf_dataset = tf_dataset.padded_batch(batch_size, padded_shapes=([299, 299, 3], [None], []))
        tf_dataset = tf_dataset.prefetch(buffer_size=tf.data.experimental.AUTOTUNE)
        return tf_dataset

    @staticmethod
    def _dataset_mapper(image_path, sequence, sequence_length):
        img = tf.io.read_file(image_path)
        img = tf.image.decode_jpeg(img, channels=3)
        img = tf.cast(img, dtype=tf.float32)
        img = tf.image.resize(img, [299, 299])
        return img, sequence, sequence_length
