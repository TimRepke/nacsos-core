from fastapi import APIRouter
from nacsos_data.schemas.annotations import AnnotationTaskModel, AnnotationTaskLabel, AnnotationTaskLabelChoice

router = APIRouter()


@router.get('/task_definition/{task_id}', response_model=AnnotationTaskModel)
async def get_task_definition(task_id: int) -> AnnotationTaskModel:
    """
    This endpoint returns the detailed definition of an annotation task.

    :param task_id: database id of the annotation task.
    :return: a single annotation task
    """
    ATLC = AnnotationTaskLabelChoice
    return AnnotationTaskModel(
        id=task_id, project_id=1, name='Test Task', description='This is a test.',
        labels=[
            AnnotationTaskLabel(name='Relevance', key='rel', kind='bool'),
            AnnotationTaskLabel(name='Claims', key='claim', kind='single', max_repeat=2,
                                choices=[
                                    ATLC(name='Global Warming is not Happening', value=0,
                                         child=AnnotationTaskLabel(
                                             name='Sub-claims',
                                             key='sub-gw', kind='single',
                                             choices=[ATLC(name='Ice isn\'t melting', value=0),
                                                      ATLC(name='Heading to ice age', value=1),
                                                      ATLC(name='Weather is cold', value=2),
                                                      ATLC(name='Hiatus is warming', value=3)]
                                         )),
                                    ATLC(name='Human GG are not causing global warming', value=1,
                                         child=AnnotationTaskLabel(
                                             name='Sub-claims',
                                             key='sub-gg', kind='single',
                                             choices=[ATLC(name='It\'s natural cycles', value=0),
                                                      ATLC(name='Non-GHG forcings', value=1),
                                                      ATLC(name='No evidence for GH effect', value=2),
                                                      ATLC(name='CO2 not rising', value=3)]
                                         ))
                                ])
        ])


@router.get('/project_tasks/{project_id}', response_model=list[AnnotationTaskModel])
async def get_task_definitions_for_project(project_id: int) -> list[AnnotationTaskModel]:
    """
    This endpoint returns the detailed definitions of all annotation tasks associated with a project.

    :param project_id: database id of the project
    :return: list of annotation tasks
    """
    return [
        AnnotationTaskModel(id=2, project_id=project_id, name='Annotation Task 1', description='This is a test (1).',
                            labels=[AnnotationTaskLabel(name='Relevance', key='rel', kind='bool')]),
        AnnotationTaskModel(id=3, project_id=project_id, name='Annotation Task 2', description='This is a test (2).',
                            labels=[AnnotationTaskLabel(name='Relevance', key='rel', kind='bool')]),
        AnnotationTaskModel(id=4, project_id=project_id, name='Annotation Task 3', description='This is a test (3).',
                            labels=[AnnotationTaskLabel(name='Relevance', key='rel', kind='bool')])]
