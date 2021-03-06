""" Simple distributed implementation of the K-Means algorithm using Tensorflow.
"""

import tensorflow as tf
import tensorframes as tfs
from pyspark.mllib.random import RandomRDDs
import numpy as np

num_features = 4
k = 2
# TODO: does not work with 1
data = RandomRDDs.normalVectorRDD(
    sc,
    numCols=num_features,
    numRows=100,
    seed=1).map(lambda v: [v.tolist()])
df = sqlContext.createDataFrame(data).toDF("features")

# For now, analysis is still required.
df0 = tfs.analyze(df)

init_centers = np.random.randn(k, num_features)

# For debugging
block = np.array(data.take(10))[::,0,::]

# Find the distances first
with tf.Graph().as_default() as g:
    points = tf.placeholder(tf.double, shape=[None, num_features], name='points')
    num_points = tf.shape(points)[0]
    #centers = tf.placeholder(tf.double, shape=[k, num_features], name='centers')
    centers = tf.constant(init_centers)
    squares = tf.reduce_sum(tf.square(points), reduction_indices=1)
    center_squares = tf.reduce_sum(tf.square(centers), reduction_indices=1)
    prods = tf.matmul(points, centers, transpose_b = True)
    t1a = tf.expand_dims(center_squares, 0)
    t1b = tf.pack([num_points, 1])
    t1 = tf.tile(t1a, t1b)
    t2a = tf.expand_dims(squares, 1)
    t2b = tf.pack([1, k])
    t2 = tf.tile(t2a, t2b)
    distances = t1 + t2 - 2 * prods
    indexes = tf.argmin(distances, 1)
    sess = tf.Session()
    print sess.run([distances, indexes], feed_dict={points:block, centers:init_centers})

with tf.Graph().as_default() as g:
    points = tf.placeholder(tf.double, shape=[None, num_features], name='features')
    num_points = tf.shape(points)[0]
    centers = tf.constant(init_centers)
    squares = tf.reduce_sum(tf.square(points), reduction_indices=1)
    center_squares = tf.reduce_sum(tf.square(centers), reduction_indices=1)
    prods = tf.matmul(points, centers, transpose_b = True)
    t1a = tf.expand_dims(center_squares, 0)
    t1b = tf.pack([num_points, 1])
    t1 = tf.tile(t1a, t1b)
    t2a = tf.expand_dims(squares, 1)
    t2b = tf.pack([1, k])
    t2 = tf.tile(t2a, t2b)
    distances = t1 + t2 - 2 * prods
    # TODO cast
    indexes = tf.argmin(distances, 1, name='indexes')
    min_distances = tf.reduce_min(distances, 1, name='min_distances')
    counts = tf.tile(tf.constant([1]), tf.pack([num_points]), name='count')
    df2 = tfs.map_blocks([indexes, counts, min_distances], df0)

# Perform the reduction
gb = df2.groupBy("indexes")
with tf.Graph().as_default() as g:
    # Look at the documentation of tfs.aggregate for the naming conventions of the placeholders.
    x_input = tfs.block(df2, "features", tf_name="features_input")
    count_input = tfs.block(df2, "count", tf_name="count_input")
    md_input = tfs.block(df2, "min_distances", tf_name="min_distances_input")
    x = tf.reduce_sum(x_input, [0], name='features')
    count = tf.reduce_sum(count_input, [0], name='count')
    min_distances = tf.reduce_sum(md_input, [0], name='min_distances')
    df3 = tfs.aggregate([x, count, min_distances], gb)

# Get the new centroids
df3_c = df3.collect()
new_centers = np.array([np.array(row.features) / row['count'] for row in df3_c])
total_distances = np.sum([row['min_distances'] for row in df3_c])


def run_one_step(dataframe, start_centers):
    """
    Performs one iteration of K-Means.

    This function takes a dataframe with dense feature vectors, a set of centroids, and returns
    a new set of centroids along with the total distance of points to centroids.

    This function calculates for each point the closest centroid and then aggregates the newly
    formed clusters to find the new centroids.

    :param dataframe: a dataframe containing a column of features (an array of doubles)
    :param start_centers: a k x m matrix with k the number of centroids and m the number of features
    :return: a k x m matrix, and a positive double
    """
    # The dimensions in the problem
    (num_centroids, num_features) = np.shape(start_centers)
    # For each feature vector, compute the nearest centroid and the distance to that centroid.
    # The index of the nearest centroid is stored in the 'indexes' column.
    # We also add a column of 1's that will be reduced later to count the number of elements in
    # each cluster.
    with tf.Graph().as_default() as g:
        # The placeholder for the input: we use the block format
        points = tf.placeholder(tf.double, shape=[None, num_features], name='features')
        # The shape of the block is extracted as a TF variable.
        num_points = tf.shape(points)[0]
        # The centers are embedded in the TF program.
        centers = tf.constant(start_centers)
        # Computation of the minimum distance. This is a standard implementation that follows
        # what MLlib does.
        squares = tf.reduce_sum(tf.square(points), reduction_indices=1)
        center_squares = tf.reduce_sum(tf.square(centers), reduction_indices=1)
        prods = tf.matmul(points, centers, transpose_b = True)
        t1a = tf.expand_dims(center_squares, 0)
        t1b = tf.pack([num_points, 1])
        t1 = tf.tile(t1a, t1b)
        t2a = tf.expand_dims(squares, 1)
        t2b = tf.pack([1, num_centroids])
        t2 = tf.tile(t2a, t2b)
        distances = t1 + t2 - 2 * prods
        # The outputs of the program.
        # The closest centroids are extracted.
        indexes = tf.argmin(distances, 1, name='indexes')
        # This could be done based on the indexes as well.
        min_distances = tf.reduce_min(distances, 1, name='min_distances')
        counts = tf.tile(tf.constant([1]), tf.pack([num_points]), name='count')
        df2 = tfs.map_blocks([indexes, counts, min_distances], dataframe)
    # Perform the reduction: we regroup the point by their centroid indexes.
    gb = df2.groupBy("indexes")
    with tf.Graph().as_default() as g:
        # Look at the documentation of tfs.aggregate for the naming conventions of the placeholders.
        x_input = tfs.block(df2, "features", tf_name="features_input")
        count_input = tfs.block(df2, "count", tf_name="count_input")
        md_input = tfs.block(df2, "min_distances", tf_name="min_distances_input")
        # Each operation is just the sum.
        x = tf.reduce_sum(x_input, [0], name='features')
        count = tf.reduce_sum(count_input, [0], name='count')
        min_distances = tf.reduce_sum(md_input, [0], name='min_distances')
        df3 = tfs.aggregate([x, count, min_distances], gb)
    # Get the new centroids
    df3_c = df3.collect()
    # The new centroids.
    new_centers = np.array([np.array(row.features) / row['count'] for row in df3_c])
    total_distances = np.sum([row['min_distances'] for row in df3_c])
    return (new_centers, total_distances)

def kmeans(dataframe, init_centers, num_iters = 50):
    c = init_centers
    d = np.Inf
    ds = []
    for i in range(num_iters):
        (c1, d1) = run_one_step(dataframe, c)
        print "Step =", i, ", overall distance = ", d1
        c = c1
        if d == d1:
            break
        d = d1
        ds.append(d1)
    return c, ds

c, ds = kmeans(df0, init_centers)
