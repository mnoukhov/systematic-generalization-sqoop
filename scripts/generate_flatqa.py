"""Generates a dataset.

The algorithm:
  GIVEN: a set of allowed object for the current dataset,
         the desired number of examples
  REPEAT:
    - decide if the answer will be yes or no
    - if yes
      - generate a random scene for which the answer can be yes using
        rejection sampling
      - select an allowed object for the generated scene
      - ask a question about it
    - if no
      - generate a random scene
      - generate an allowed (shape, color) pair for which the answer is no
        for the given scene using rejection sampling
      - ask the question about the selected (shape, color) objects

"""
import numpy
import pygame
import argparse
import h5py
import pygame
import collections
import json
import logging
import time
import math

logger = logging.getLogger(__name__)


COLOR2RGB = [('red', (255, 0, 0)),
             ('green', (0, 255, 0)),
             ('blue', (0, 0, 255)),
             ('yellow', (255, 255, 0)),
             ('cyan', (0, 255, 255)),
             ('purple', (128, 0, 128)),
             ('brown', (165, 42, 42)),
             ('gray', (128, 128, 128))]
COLOR2RGB = collections.OrderedDict(COLOR2RGB)
COLORS = list(COLOR2RGB.keys())
SHAPES = ['square', 'empty_square', 'circle',
          'triangle', 'empty_triangle', 'cross', 'bar']
MIN_OBJECT_SIZE = 8


def draw_object(shape, color, surf):
  width, height = surf.get_size()
  if width != height:
    raise ValueError("can only draw on square cells")
  if width < MIN_OBJECT_SIZE:
    raise ValueError("too small, can't draw")
  rgb = COLOR2RGB[color]

  if shape == 'square':
      pygame.draw.rect(surf, rgb, pygame.Rect(0, 0, width, height))
  elif shape == 'circle':
      pygame.draw.circle(surf, rgb, (width // 2, height // 2), width // 2)
  elif shape == 'triangle':
      polygon = [(0, 0),
                  (width // 2, height - 1),
                  (width - 1, 0)]
      pygame.draw.polygon(surf, rgb, polygon)
  elif shape == 'empty_triangle':
      polygon = [(0, 0),
                  (width // 2, height - 1),
                  (width - 1, 0)]
      pygame.draw.polygon(surf, rgb, polygon, width // 4 - 1)
  elif shape == 'empty_square':
      thickness = width // 4 - 1
      polygon = [(thickness - 1, thickness - 1),
                  (width - thickness, thickness - 1),
                  (width - thickness, height - thickness),
                  (thickness - 1, height - thickness)]
      pygame.draw.polygon(surf, rgb, polygon, thickness)
  elif shape == 'cross':
      pygame.draw.line(surf, rgb, (0, 0), (width - 1, height - 1), width // 4)
      pygame.draw.line(surf, rgb, (width - 1, 0), (0, height - 1), width // 4)
  elif shape == 'bar':
      pygame.draw.line(surf, rgb, (0, height // 2), (width - 1, height // 2), width // 4)
  else:
      raise ValueError()


def get_object_bitmap(shape, color, size):
  surf = pygame.Surface((size, size))
  draw_object(shape, color, surf)
  return surf


def surf2array(surf):
  arr = pygame.surfarray.array3d(surf)
  arr = arr.transpose(1, 0, 2)
  arr = arr[::-1, :, :]
  return arr

Object = collections.namedtuple(
  'Object', ['pos', 'size', 'shape', 'color'])


class SceneGenerator:

  def __init__(self, size, num_objects, seed,
               object_allowed):
    self._size = size
    self._num_objects = num_objects
    self._seed = seed
    self._rng = numpy.random.RandomState(seed)
    self._object_allowed = object_allowed

  def __iter__(self):
    self._rng = numpy.random.RandomState(self._seed)
    return self

  def __next__(self):
    return self.generate_scene()

  def generate_scene(self):
    surface = pygame.Surface((self._size, self._size))
    objects = []

    place_failures = 0
    while len(objects) < self._num_objects:
      # first, select which object to draw by rejection sampling
      while True:
        shape = self._rng.choice(SHAPES)
        color = self._rng.choice(COLORS)
        if self._object_allowed((shape, color), 'generate'):
          break

      # then, select the object size
      obj_size = self._rng.randint(MIN_OBJECT_SIZE, 2 * MIN_OBJECT_SIZE)

      min_center = obj_size / 2 + 1
      max_center = self._size - obj_size / 2 - 1
      placed = False
      for attempt in range(10):
        x = self._rng.randint(min_center, max_center)
        y = self._rng.randint(min_center, max_center)

        # check if there is no overlap between bounding squares
        overlap = False
        for other in objects:
          min_dist = (obj_size + other.size) + 1
          if (abs(x - other.pos[0]) + abs(y - other.pos[1]) < min_dist):
            overlap = True
            break

        if not overlap:
          objects.append(Object(pos=(x, y), size=obj_size,
                                shape=shape, color=color))
          bitmap = get_object_bitmap(shape, color, obj_size)
          surface.blit(source=bitmap, dest=(x - obj_size / 2, y - obj_size / 2))
          placed = True
          break

      if not placed:
        place_failures += 1
        if place_failures == 10:
          # this recursive call seems to be the easiest way to just start
          # generation from scratch
          return self.generate_scene()

    return objects, surface


def generate_dataset(prefix, size, num_examples, seed, object_allowed, save_vocab=False):
  sg = SceneGenerator(size=size, num_objects=5, seed=1, object_allowed=object_allowed)

  max_question_len = 5
  max_program_len = 7

  question_words = (['<NULL>', '<START>', '<END>', 'is', 'there', 'a']
                    + sorted(list(COLOR2RGB))
                    + SHAPES)
  question_vocab = {word: i for i, word in enumerate(question_words)}

  program_words = (['<NULL>', '<START>', '<END>', 'scene', 'And']
                   + sorted(list(COLOR2RGB))
                   + SHAPES)
  program_vocab = {word: i for i, word in enumerate(program_words)}

  scenes = []

  # generate questions
  before = time.time()
  with h5py.File(prefix + '_questions.h5', 'w') as dst_questions,\
       h5py.File(prefix + '_features.h5', 'w') as dst_features:
    features_dataset = dst_features.create_dataset(
      'features', (num_examples, 3, size, size), dtype=numpy.float32)
    questions_dataset = dst_questions.create_dataset(
      'questions', (num_examples, max_question_len), dtype=numpy.int64)
    programs_dataset = dst_questions.create_dataset(
      'programs', (num_examples, max_program_len), dtype=numpy.int64)
    answers_dataset = dst_questions.create_dataset(
      'answers', (num_examples,), dtype=numpy.int64)
    image_idxs_dataset = dst_questions.create_dataset(
      'image_idxs', (num_examples,), dtype=numpy.int64)

    rng = numpy.random.RandomState(seed)
    i = 0
    for scene, surface in sg:
      if i == num_examples:
        break

      answer = i % 2
      if answer:
        candidate_objects = [(obj.shape, obj.color) for obj in scene
                             if object_allowed((obj.shape, obj.color), 'ask')]
        if not candidate_objects:
          # can't generate a positive question about this scene
          continue
        shape, color = candidate_objects[rng.randint(len(candidate_objects))]
      else:
        # sample an allowed (shape, color) pair that is not present in the picture
        # if failed 10 times, try another scene
        for attempt in range(11):
          shape = rng.choice(SHAPES)
          color = rng.choice(COLORS)
          if not object_allowed((shape, color), 'ask'):
            continue
          found = any((shape, color) == (obj.shape, obj.color)
                      for obj in scene)
          if not found:
            break
        if attempt == 10:
          continue

      question = ["is", "there", "a"] + [color, shape]
      program = ['<START>', 'And', shape, 'scene', color, 'scene', '<END>']

      scenes.append(scene)
      features_dataset[i] = surf2array(surface).transpose(2, 0, 1) / 255.0
      questions_dataset[i] = [question_vocab[w] for w in question]
      programs_dataset[i] = [program_vocab[w] for w in program]
      answers_dataset[i] = int(answer)
      image_idxs_dataset[i] = i

      i += 1
  print("{} examples per second".format((time.time() - before) / num_examples))

  with open(prefix + '_scenes.json', 'w') as dst:
    json.dump(scenes, dst, indent=2)

  if save_vocab:
    def arity(token):
      if token == 'And':
        return 2
      elif token == 'scene':
        return 0
      else:
        return 1
    with open('vocab.json', 'w') as dst:
      json.dump({'question_token_to_idx': question_vocab,
                'program_token_to_idx': program_vocab,
                'program_token_arity':
                    {name: arity(name) for name in program_vocab},
                  'answer_token_to_idx':
                    {'false': 0, 'true': 1}},
                dst)


def main():
  train_object_allowed = val_object_allowed = test_object_allowed = lambda _1, _2: True

  class ObjectRestriction:
    def __init__(self, inverse=False, restrict_scene=True):
      """Base class for object restrictions.

      inverse: if True, invert the output of `self.allow`
      restrict_scene: if True, make sure that excluded objects are not
        generated
      """
      self._inverse = inverse
      self._restrict_scene = restrict_scene
    def __call__(self, obj, purpose):
      if not self._restrict_scene and purpose == 'generate':
        return True
      if self._inverse:
        return self.allow(obj, purpose)
      else:
        return not self.allow(obj, purpose)
    def allow(self, obj, puprose):
      raise NotImplementedError()


  if args.split == 'CoGenT':
    class RestrictSquaresAndTriangles:
      def __init__(self, square_colors, triangle_colors, test=False):
        self._square_colors = square_colors
        self._triangle_colors = triangle_colors
        self._test = test
      def __call__(self, obj, purpose):
        shape, color = obj
        if shape == 'square':
          return color in self._square_colors
        elif shape == 'triangle':
          return color in self._triangle_colors
        # at the test time we want to ask questions only about the special
        # objects that were not include in the training set
        if self._test and not purpose == 'generate':
          return False
        return True

    set1 = ['gray', 'blue', 'brown', 'yellow']
    set2 = ['red', 'green', 'purple', 'cyan']

    train_object_allowed = RestrictSquaresAndTriangles(set1, set2)
    val_object_allowed = test_object_allowed = RestrictSquaresAndTriangles(set2, set1,
                                                                           test=True)
  if args.split == 'diagonal':
    class ExcludeDiagonal:
      def __init__(self, test=False):
        self._test = test
      def __call__(self, obj, purpose):
        shape, color = obj
        shape_number = SHAPES.index(shape)
        color_number = COLORS.index(color)
        diagonal = shape_number == color_number
        if self._test:
          return purpose == 'generate' or diagonal
        else:
          return not diagonal
    train_object_allowed = ExcludeDiagonal()
    val_object_allowed = test_object_allowed = ExcludeDiagonal(test=True)

  if args.split == 'leave1out':
    class ExcludeRedSquare(ObjectRestriction):
      def allow(self, obj, purpose):
        return obj == ('square', 'red')
    train_object_allowed = ExcludeRedSquare(restrict_scene=args.restrict_scene)
    val_object_allowed = test_object_allowed = ExcludeRedSquare(
      inverse=True, restrict_scene=False)

  generate_dataset('train', args.image_size,
                   args.train, 1, train_object_allowed, save_vocab=True)
  generate_dataset('val', args.image_size,
                   args.val, 2, val_object_allowed)
  generate_dataset('test', args.image_size,
                   args.test, 3, test_object_allowed)


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--train', type=int, default=1000,
                      help="Size of the training set")
  parser.add_argument('--val', type=int, default=100,
                      help="Size of the development set")
  parser.add_argument('--test', type=int, default=100,
                      help="Size of the test set")
  parser.add_argument('--image-size', type=int, default=50)
  parser.add_argument('--split', type=str,
                      choices=('none', 'CoGenT', 'diagonal', 'leave1out'),
                      help="The split to use")
  parser.add_argument('--restrict-scene', type=int, default=1,
                      help="Make sure that held-out objects do not appeat in the scene"
                            "during training")
  args = parser.parse_args()
  main()