import express from 'express';
import mongoose from 'mongoose';
import { ObjectId } from 'mongodb';

if (!process.env.MONGODB_URI) throw new Error('MONGODB_URI is required');
await mongoose.connect(process.env.MONGODB_URI);
const db = mongoose.connection.db;
const app = express();
app.use(express.json());
app.use((request, _response, next) => {
  console.log(JSON.stringify({method: request.method, path: request.path}));
  next();
});
app.get('/api/health', async (_request, response, next) => {
  try {
    await db.admin().ping();
    response.json({status: 'ok', db: 'connected'});
  } catch (error) { next(error); }
});
app.get('/api/tenants', async (request, response, next) => {
  try {
    const limit = Math.min(Number(request.query.limit || 20), 100);
    const filter = request.query.cursor ? { _id: { $gt: new ObjectId(String(request.query.cursor)) } } : {};
    const items = await db.collection('tenants').find(filter).sort({_id: 1}).limit(limit).toArray();
    response.json({items, nextCursor: items.length === limit ? String(items.at(-1)._id) : null});
  } catch (error) { next(error); }
});
app.get('/api/documents', async (request, response, next) => {
  try {
    const limit = Math.min(Number(request.query.limit || 20), 100);
    const filter = request.query.cursor ? { _id: { $gt: new ObjectId(String(request.query.cursor)) } } : {};
    const items = await db.collection('documents').find(filter).sort({_id: 1}).limit(limit).toArray();
    response.json({items, nextCursor: items.length === limit ? String(items.at(-1)._id) : null});
  } catch (error) { next(error); }
});
app.get('/api/chunk_profiles', async (request, response, next) => {
  try {
    const limit = Math.min(Number(request.query.limit || 20), 100);
    const filter = request.query.cursor ? { _id: { $gt: new ObjectId(String(request.query.cursor)) } } : {};
    const items = await db.collection('chunk_profiles').find(filter).sort({_id: 1}).limit(limit).toArray();
    response.json({items, nextCursor: items.length === limit ? String(items.at(-1)._id) : null});
  } catch (error) { next(error); }
});
app.get('/api/chunks', async (request, response, next) => {
  try {
    const limit = Math.min(Number(request.query.limit || 20), 100);
    const filter = request.query.cursor ? { _id: { $gt: new ObjectId(String(request.query.cursor)) } } : {};
    const items = await db.collection('chunks').find(filter).sort({_id: 1}).limit(limit).toArray();
    response.json({items, nextCursor: items.length === limit ? String(items.at(-1)._id) : null});
  } catch (error) { next(error); }
});
app.get('/api/queries', async (request, response, next) => {
  try {
    const limit = Math.min(Number(request.query.limit || 20), 100);
    const filter = request.query.cursor ? { _id: { $gt: new ObjectId(String(request.query.cursor)) } } : {};
    const items = await db.collection('queries').find(filter).sort({_id: 1}).limit(limit).toArray();
    response.json({items, nextCursor: items.length === limit ? String(items.at(-1)._id) : null});
  } catch (error) { next(error); }
});
app.get('/api/benchmark_runs', async (request, response, next) => {
  try {
    const limit = Math.min(Number(request.query.limit || 20), 100);
    const filter = request.query.cursor ? { _id: { $gt: new ObjectId(String(request.query.cursor)) } } : {};
    const items = await db.collection('benchmark_runs').find(filter).sort({_id: 1}).limit(limit).toArray();
    response.json({items, nextCursor: items.length === limit ? String(items.at(-1)._id) : null});
  } catch (error) { next(error); }
});
app.get('/api/query_results', async (request, response, next) => {
  try {
    const limit = Math.min(Number(request.query.limit || 20), 100);
    const filter = request.query.cursor ? { _id: { $gt: new ObjectId(String(request.query.cursor)) } } : {};
    const items = await db.collection('query_results').find(filter).sort({_id: 1}).limit(limit).toArray();
    response.json({items, nextCursor: items.length === limit ? String(items.at(-1)._id) : null});
  } catch (error) { next(error); }
});
app.use((error, _request, response, _next) => {
  console.error(error);
  response.status(500).json({error: {code: 'INTERNAL_ERROR', message: error.message}});
});
app.listen(Number(process.env.PORT || 8080), '127.0.0.1');
