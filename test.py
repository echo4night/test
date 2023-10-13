from vcScript import *
from vcHelpers.Selection import *
from vcHelpers.Math import *
import vcMatrix, vcVector
import math
import random
import vcHelpers.Robot2
from vcHelpers.Robot2 import *

useTracing(False)

app = getApplication()
sim = getSimulation()
world = sim.World #world node
comp = getComponent()
node = getNode()
RAD_TO_DEG = 180/math.pi
ROOT = sim.World
ROOT_cont = [x for x in ROOT.Behaviours if x.Type == 'rSimContainer'][0]
finalized = False

def OnSignal(signal): #When "Task" signal is sent to comp, it is added to queue2 list as a list
  global comp, queue2, tasksignal
  if signal == tasksignal:
    val = signal.Value
    queue2.append(val.split("&"))

def goIdle(): #move AGV to first free idle position with (partially) matching name <> string parameter IdlePositionFilter
  ##Future development: Add break possibility if new task arrives while AGV is moving to idle position
  global idlepositions, atIdle
  humanState("Idle")
  idlepositions.sort( key = lambda x: (x[2].P-comp.WorldPositionMatrix.P).length() )
  for x in idlepositions:
    if comp.IdlePositionFilter in x[0].Name and x[1].Value < x[0].Capacity and (comp.WorldPositionMatrix.P - x[2].P).length() > 300:
      atIdle = x[0]
      x[1].Value += 1
      move(x[2])
      break
  else:
    # Go to charging
    idle_position_filter = [x.strip() for x in comp.IdlePositionFilter.split(",")]
    for pos in idle_position_filter:
      if pos in p_filter:
        goCharge(idle_charging=True)

def goCharge(idle_charging=False): #move AGV to first free charge position with (partially) matching name <> string parameter ReChargePositionFilter
  ##Future development: Add break possibility if new task arrives while AGV is moving to idle position
  global chargepositions, p_currentcapacity, p_capacity, p_busy, p_idle, p_rate, p_filter
  global timestamp
  global idlepositions, atIdle
  global powerAutoFilled

  humanState("Charging")
  chargepositions.sort( key = lambda x: (x[2].P-comp.WorldPositionMatrix.P).length() )
  chargeposition = False
  goingidlewait = False

  while chargeposition == False:
    filtered_charge_stations = [x for x in chargepositions if p_filter in x[0].Name]
    if not len(filtered_charge_stations):
      if not powerAutoFilled:
        print "WARNING - Out of power! No charging station found for '{}'. Continue (Play) simulation to fill capacity to max instantly!".format(comp.Name)
        sim.halt()
      else:
        print "WARNING - Out of power! No charging station found for '{}'. Capacity filled to max instantly!".format(comp.Name)
      powerAutoFilled = True
      p_currentcapacity.Value = p_capacity
      timestamp = sim.SimTime
      return
    for x in filtered_charge_stations:
      if chargeposition == False and p_filter in x[0].Name and x[1].Value < x[0].Capacity and (comp.WorldPositionMatrix.P - x[2].P).length() > 300:
        #atIdle = x[0]
        x[1].Value += 1
        chargeposition = True
        if goingidlewait == True:  # was at idle location waiting for available charging station
          i = atIdle
          i.Occupied -=1
          atIdle = None
        move(x[2])

        rechargetime = ((p_capacity * p_charge_until_limit - p_currentcapacity.Value) / p_rate) * 3600.0
        if idle_charging:
          # Charging station used for idling (and charging)
          atIdle = x[0]
          timestamp = sim.SimTime
          return
        else:
          if comp.SimulationLevel == VC_SIMULATION_FAST:
            delay(rechargetime)
          else:
            rechargetime, rest = divmod(rechargetime, stat_interval)
            for i in range(int(rechargetime)):
              delay(stat_interval)
              p_currentcapacity.Value += (p_capacity * p_charge_until_limit) / rechargetime
            delay(rest)
          p_currentcapacity.Value = p_capacity * p_charge_until_limit
          timestamp = sim.SimTime
          x[1].Value -= 1
          break
        
    if chargeposition == False: #if no free change position found, go to idle position to wait. NO NEW TASKS WHEN NO BATTERY
      idlepositions.sort( key = lambda x: (x[2].P-comp.WorldPositionMatrix.P).length() )
      for x in idlepositions:
        if goingidlewait == False and comp.IdlePositionFilter in x[0].Name and x[1].Value < x[0].Capacity and (comp.WorldPositionMatrix.P - x[2].P).length() > 300:
          atIdle = x[0]
          x[1].Value += 1
          move(x[2])
          goingidlewait = True
          break
      delay(1.0)

def matInterpolator(increment,mat1,mat2): #increment should be strength multiplier between 0-1... essentially mat1 - mat2 power
  m1 = vcMatrix.new(mat1)
  m1i = vcMatrix.new(m1)
  m1i.invert()
  m2 = vcMatrix.new(mat2)
  mdiff = m1i * m2 # relative from m1 -> m2
  pos = increment * mdiff.P # interpolation of position
  aa = mdiff.getAxisAngle() # interpolation of orientation
  aa.W = increment * aa.W
  m1.translateRel(pos.X,pos.Y,pos.Z)
  m1.rotateRelV(aa,aa.W)
  return m1

def OnFinalize(): #hides all unlisted parameters from the component
  global finalized
  finalized = True

def RunServoMovements(matrix = None, timer = 1.0, loc = None): #additional axis movements if any
  returntime = 0
  if servo_lift and comp.SimulationLevel != VC_SIMULATION_FAST:
    if loc != None:
      if loc > lift_max_limit: loc = lift_max_limit
      if loc < lift_min_limit: loc = lift_min_limit
      servo_lift.setJointTarget(0,loc)
      servo_lift.setMotionTime(timer)
      returntime += timer
      servo_lift.move()
    if matrix != None:
      locd = matrix.P.Z - comp.WorldPositionMatrix.P.Z - PatternLocation.Value.P.Z
      if locd > lift_max_limit: locd = lift_max_limit
      if locd < lift_min_limit: locd = lift_min_limit
      servo_lift.setJointTarget(0,locd)
      servo_lift.setMotionTime(timer)
      returntime += timer
      servo_lift.move()
  return returntime #return time consumed here

def OnStart():
  global idlepositions, visualize_walk_prop, walk_dist_prop #Move related
  global updatetime, oldtime
  global bnode
  global tcont, PatternLocation
  global stats
  global opt_animate, opt_avoidance, opt_pathfinding
  global PatternSlots, PatternLocations
  global tuglist, tugcomps, tuglocations
  global cartlist, cartcomps
  global chargepositions, p_currentcapacity, p_capacity, p_busy, p_idle, p_rate, p_filter
  global robo, robot_controller, robot_tool_iface, robot_home_joint_vals
  global taskcontrolupdate
  global powerAutoFilled, p_charge_until_limit, p_to_charge_limit
  
  powerAutoFilled = False

  # find Works Task Control
  taskcontrol = getApplication().findComponent("Works_TaskControl")
  taskcontrolupdate = None
  if not taskcontrol:
    return
  taskcontrolupdate = taskcontrol.findBehaviour("Task") #update signal based works task control

  # Seed random for (battery charge initial level)
  random.seed(taskcontrol.WorksRandomSeed)

  #check do we have robot arms onboard (attached)
  robo = None
  robot_controller = None
  robot_tool_iface = None
  robot_home_mtx = None
  for child in comp.ChildComponents:
    controllers = child.findBehavioursByType(VC_ROBOTCONTROLLER)
    if controllers:
      robot = child
      robo = getRobot(robot)
      robot_controller = robo.Controller
      robot_tool_iface = robot_controller.FlangeNode.getBehavioursByType(VC_ONETOONEINTERFACE)[0]
      updateRobotConfigurationPropList(robo)
      robot_home_joint_vals = [j.CurrentValue for j in robo.Joints]
      break

  showHideRobotTab(robo)

  #create pattern
  PatternStep = comp.getProperty("AGV::PatternStep")
  PatternLocation = comp.getProperty("AGV::PatternLocation")
  Pattern = comp.getProperty("AGV::Pattern")
  PatternAmount = int(Pattern.Value.X) * int(Pattern.Value.Y) * int(Pattern.Value.Z)
  PatternSlots = [None] * PatternAmount
  PatternLocations = []
  stack_frame = comp.findFeature("Stack")
  target = stack_frame.NodePositionMatrix

  for i in range(PatternAmount):
    loc = vcMatrix.new(target)
    Pos = loc.P
    moveZ=0
    level = int(Pattern.Value.X) * int(Pattern.Value.Y)
    moveZ = i / level
    index2 = i-level*moveZ
    ret = divmod(index2, int(Pattern.Value.Y))
    Pos.X += int(PatternStep.Value.X) * ret[0]
    Pos.Y += int(PatternStep.Value.Y) * ret[1]
    Pos.Z += int(PatternStep.Value.Z) * moveZ
    loc.P = Pos
    PatternLocations.append(loc)

  opt_animate = comp.getProperty("Optimization::Animate") #no used, no animation at this point
  opt_avoidance = comp.getProperty("Optimization::Avoidance")
  opt_pathfinding = comp.getProperty("Optimization::Pathfinding")

  #check which ProdID components are tugged
  tug = comp.getProperty("AGV::TugIDList").Value
  tuglist = [x.strip() for x in tug.split(",")]
  tugcomps = []
  tuglocations = [comp.WorldPositionMatrix]

  tcont = comp.findBehaviour("TargetContainer__HIDE__")

  #check which components are carts
  cart = comp.getProperty("AGV::Carts").Value
  cartlist = [x.strip() for x in cart.split(",")]
  cartcomps = []
  for cart in cartlist:
    c = app.findComponent(cart)
    if c:
      cartcomps.append(c)
      tcont.grab(c)

  comp.RunningRoute = "" #Running Route is used to check is a resource in the middle of the route

  stats = comp.findBehaviour("Statistics")
  states = [("Processing",VC_STATISTICS_BUSY),
  ("Repairing",VC_STATISTICS_BUSY),
  ("Assisting",VC_STATISTICS_BUSY),
  ("Transporting",VC_STATISTICS_BUSY),
  ("Picking",VC_STATISTICS_BUSY),
  ("Placing",VC_STATISTICS_BUSY),
  ("Moving",VC_STATISTICS_BUSY),
  ("CollectingTool",VC_STATISTICS_BUSY),
  ("ReturningTool",VC_STATISTICS_BUSY),
  ("Idle",VC_STATISTICS_IDLE),
  ("Blocked",VC_STATISTICS_BLOCKED),
  ("Bypassing",VC_STATISTICS_BLOCKED),
  ("Charging",VC_STATISTICS_SETUP),
  ("Break",VC_STATISTICS_BREAK)]

  if stats.States != states:
    stats.States = states

  bnode = comp.findNode("State") #bnode is visual color representation of the stats state
  if bnode:
    bnode.MaterialInheritance = VC_MATERIAL_FORCE_INHERIT

  #recharging parameters
  #power ["Power::Capacity","Power::InitialCapacity","Power::RandomInitialCapacity","Power::BusyConsumption_h","Power::IdleConsumption_h","Power::ReChargeRate_h","Power::ReChargePositionFilter"]
  p_capacity = comp.getProperty("Power::Capacity").Value
  p_initcapacity = comp.getProperty("Power::InitialCapacity").Value
  p_random = comp.getProperty("Power::RandomInitialCapacity").Value
  p_busy = comp.getProperty("Power::BusyConsumption_h").Value
  p_idle = comp.getProperty("Power::IdleConsumption_h").Value
  p_rate = comp.getProperty("Power::ReChargeRate_h").Value
  p_filter = comp.getProperty("Power::ReChargePositionFilter").Value
  p_currentcapacity = comp.getProperty("Power::CurrentCapacity")
  p_charge_until_limit = comp.getProperty("Power::ChargeUntilLimit").Value
  p_to_charge_limit = comp.getProperty("Power::ToChargeLimit").Value
  

  if p_random: p_currentcapacity.Value = random.random() * p_capacity
  else: p_currentcapacity.Value = p_initcapacity

  #Charge positions
  idleFN = 'AGVReCharge__Position__Frame'
  chargepositions = []
  for x in app.Components:
    if x.findFeature(idleFN):
      chargepositions.append( (x, x.getProperty('Occupied'), x.WorldPositionMatrix * x.findFeature(idleFN).NodePositionMatrix) )
      x.Occupied = 0

  updatetime = 0 #when app.render(), update this time
  oldtime = 0 #last time we have updated 3D world / comp location

  walk_dist_prop = comp.getProperty('AGV::Distance')
  walk_dist_prop.Value = 0.0
    #idle positions
  idleFN = 'AGVIdle__Position__Frame'
  idlepositions = []
  for x in app.Components:
    if x.findFeature(idleFN):
      idlepositions.append( (x, x.getProperty('Occupied'), x.WorldPositionMatrix * x.findFeature(idleFN).NodePositionMatrix) )
      x.Occupied = 0
    try: x.ReservedTool = False
    except: pass


def clearcarry(arg): #clear all frames from node. Frames define lteached location for ProdID
  node = comp.findNode("LocCont")
  root = node.RootFeature
  for fea in root.Children:
    if fea.Type == VC_FRAME:
      fea.delete()


def teachcarryframe(item, node): #teach new frame per ProdID
  name = item.Name
  if item.getProperty("ProdID"):
    name = item.ProdID
  name += "_location"
  feat = node.getFeature(name)
  if feat: feat.delete()
  f = node.RootFeature.createFeature(VC_FRAME,name)
  mat = vcMatrix.new(item.PositionMatrix)
#  mat.translateRel(0,0,-comp.AGVHeight)
  f.PositionMatrix = mat

def teachcarry(arg):#teach new frame per ProdID
  global tcont
  for item in tcont.Components:
    node = comp.findNode("LocCont")
    teachcarryframe(item, node)
    print 'Product location for "%s" defined' %(item.Name)
  comp.rebuild()

def OnSimulationUpdate(time):
  global updatetime
  updatetime = time

def OnRun():
  global moves, runningstep, pars, iterspersec
  global comp, controller
  global avoid_comps
  global global_locks
  global comp, queue2, tasksignal
  global idlepositions, atIdle
  global tcont
  global locs
  global updatetime, oldtime
  global currentarea
  global opt_animate, opt_avoidance, opt_pathfinding
  global ReservedCells, stat_interval
  global chargepositions, p_currentcapacity, p_capacity, p_busy, p_idle, p_rate, p_filter
  global timestamp #used in power capacity calculations

  if not taskcontrolupdate:
    print "ERROR in '%s': Works Task Control not found" % comp.Name
    return

  timestamp = 0
  ReservedCells = []
  currentarea = None
  stat_interval = app.Dashboard.StatisticsInterval
  queue2 = [] #list of pending tasks
  tasksignal = comp.findBehaviour("Task")

  p = comp.getProperty("Optimization::IterationsPerSecond")
  iterspersec = p.Value

  controller = None
  RequestSignal = None ########
  ReleaseSignal = None ########

  avoid_comps = [] #only for proximity test
  locs = []


  for item in getApplication().Components:
    if item.getProperty("ID") != None:
      if item.ID == "LaborLocation":
        locs.append(item)
      if item.ID == "LaborPathwaySystemController":
        controller = item
        RequestSignal = item.findBehaviour("Request")
        ReleaseSignal = item.findBehaviour("Release")
      if item.ID == "avoidance" and item != comp and opt_avoidance.Value:
        avoid_comps.append(item)

  if not controller and opt_pathfinding.Value:
    print "Error in component %s, No Pathfinder component found. Add 'Works Resource Pathfinder' into the 3D World or unselect the 'Pathfinding' option from 'Optimization' tab."%comp.Name
    suspendRun()
    return

  tasksignal = comp.findBehaviour("Task")
  paths = [] #path handles
  targets = []
  loc = comp.WorldPositionMatrix
  donePicking = False
  picked = 0
  atIdle = None

  previoustask = "FooBar"
  while True:
    #check for power capacity
    if comp.RunningRoute == "" and not queue2: #we are not middle of the route and no tasks in queue
      if p_currentcapacity.Value <= p_capacity * p_to_charge_limit: #have we run out of battery
        comp.Busy = True
        taskcontrolupdate.signal("update")
        goCharge()

    comp.Busy = False
    taskcontrolupdate.signal("update")
    humanState("Idle")
    while True:
      if not atIdle:
        triggedd = condition(lambda: queue2, comp.DelayBeforeIdle)
      else:
        triggedd = condition(lambda: queue2)#, 12)
      if triggedd:
        try:
          task = queue2.pop(0)
          comp.Busy = True
          taskcontrolupdate.signal("update")
          break
        except:
          continue
      else:
        if not atIdle and comp.RunningRoute == "":
          goIdle()
          taskcontrolupdate.signal("update")
        continue
    if atIdle:
      atIdle.Occupied -= 1 # release the occupied capacity from the idle component
      if stats.State == "Charging":
        charge_time = sim.SimTime - timestamp
        new_capacity = p_currentcapacity.Value + p_rate * charge_time / 3600.0
        new_capacity = new_capacity if new_capacity < p_capacity else p_capacity
        p_currentcapacity.Value = new_capacity
        humanState("Idle")
    atIdle = None
    start_t = sim.SimTime
    comp.update()

    if task[0] == "Wait" and previoustask != "Wait": #target or component
      waittime = task[1].strip()
      delay(float(waittime))

    if task[0] == "Move": #target or component
      humanState("Moving")
      toname = task[1].strip()
      tocomponent = app.findComponent(toname)
      
      # Move only to approach loc if such is available
      # Otherwise move to component origin
      # Affects routing, remove this if-statement if you want to force motion to comp
      for cc in tocomponent.ChildComponents:
        if cc.getProperty("ID") and (cc.ID == "Approach" or cc.ID == "Both"):
          amat = cc.WorldPositionMatrix
          move(amat)
          break
      else:
        wpm2 = tocomponent.WorldPositionMatrix

        in_node, res_loc_frame = findFeature(tocomponent,'ResourceLocation') #approach frame, closest cell
        if res_loc_frame:
          wpm2 = in_node.WorldPositionMatrix * res_loc_frame.NodePositionMatrix

        dd = wpm2.P - tocomponent.WorldPositionMatrix.P
        toCompResLoc = tocomponent.getProperty('ResourceLocation::ResourceLocation')
        if toCompResLoc and toCompResLoc.Value == 'NearestGrid':
          move(wpm2, Closest = True)
        else:
          move(wpm2)

    if task[0] == "Need": #raw need, only drop
      compname = task[1].strip() #Prod_ID

      comps = []
      for cart in cartcomps:
        cont = cart.findBehaviour("TargetContainer__HIDE__")
        comps.extend(cont.Components)
      comps.extend(tcont.Components)
      ccomp = None
      for c in comps:
        if c.ProdID == compname:
          ccomp = c
      if ccomp:
        toname = task[2].strip()
        toloc = task[3].strip()
        tocomponent = app.findComponent(toname)
        toCompResLoc = tocomponent.getProperty('ResourceLocation::ResourceLocation')
        if toCompResLoc and toCompResLoc.Value == 'RelativeToProduct':
          print 'Warning in resource',comp.Name, 'place RelativeToProduct not available in route control'

        approach = None
        deproach = None
        for cc in tocomponent.ChildComponents:
          if cc.getProperty("ID") and (cc.ID == "Approach" or cc.ID == "Both"):
            approach = cc
          if cc.getProperty("ID") and (cc.ID == "Deproach" or cc.ID == "Both"):
            deproach = cc


        tocont = tocomponent.findBehaviour("Container__HIDE__")

        autoReserveSelf(tocomponent)

        prop = tocomponent.getProperty("Advanced::PlaceCycleTime")
        placetime = prop.Value

        Unloadtime = comp.getProperty("AGV::UnloadingTime")
        if Unloadtime.Value > 0: placetime = Unloadtime.Value

        wpm2 = tocomponent.WorldPositionMatrix

        if wpm2 == tocomponent.WorldPositionMatrix:
          in_node, res_loc_frame = findFeature(tocomponent,'ResourceLocation') #approach frame, closest cell
          if res_loc_frame:
            wpm2 = in_node.WorldPositionMatrix * res_loc_frame.NodePositionMatrix

        posname = "MW_%s"%(compname)
        count_of_this_id = len( [x for x in tocomponent.ChildComponents if x.Container and x.getProperty('ProdID') and x.getProperty('ProdID').Value.strip() == compname.strip()] )
        frame_with_index = tocomponent.getFeature( '%s_%i' % ( posname , count_of_this_id )  )
        if frame_with_index:
          m1 = frame_with_index.NodePositionMatrix
          target = m1
        else:
          frame_without_index = tocomponent.getFeature(posname)
          if frame_without_index:
            m1 = frame_without_index.NodePositionMatrix
            target = m1
          else:
            tocomponent.createProperty(VC_MATRIX, 'DefaultMatrix')
            target = tocomponent.DefaultMatrix #default location
        sensorcomponent = tocomponent.getProperty("sensorcomponent")
        if sensorcomponent:
          ccomps = tocont.Components + [x for x in [sensorcomponent.Value] if x != None]
        else:
          ccomps = tocont.Components
        for cc in ccomps:
          if cc.getProperty("ChildrenInAssembly") and cc.ChildrenInAssembly == ccomp.Name:
            if ccomp.getProperty("LocationInAssembly"):
              target = cc.PositionMatrix * ccomp.LocationInAssembly

        poses = toloc.split(",")
        target.translateRel(float(poses[0]),float(poses[1]),float(poses[2]))
        if len(poses) > 3:
          target.rotateRelZ(float(poses[3]))
          target.rotateRelY(float(poses[4]))
          target.rotateRelX(float(poses[5]))
        mat = tocont.Parent.WorldPositionMatrix * target
        
        humanState("Transporting")

        dd = wpm2.P - tocomponent.WorldPositionMatrix.P

        if approach:
          amat = approach.WorldPositionMatrix
          move(amat)
        returntime = RunServoMovements(matrix = mat, timer = (placetime/4.0))
        LinearVal = False
        ClosestVal = None
        ttarget = wpm2
        if approach: LinearVal = True
        toCompResLoc = tocomponent.getProperty('ResourceLocation::ResourceLocation')
        if toCompResLoc and toCompResLoc.Value == 'NearestGrid':
          ClosestVal = True
          ttarget = mat
        move(ttarget, Closest = ClosestVal, Linear = LinearVal)

        humanState("Placing")
        if placetime <= 0: placetime = 1.0

        Place(ccomp,mat,tocomponent, timer = placetime-(returntime*2.0))

        tocont.grab(ccomp)
        keepProdOrientation = tocomponent.getProperty('Advanced::KeepProdOrientation')
        if not keepProdOrientation or (keepProdOrientation and not keepProdOrientation.Value):
          ccomp.PositionMatrix = target
          ccomp.update()

        picked -= 1

        res = ccomp.getProperty("resource_reserver")
        if res:
          ccomp.resource_reserver = ""
        returntime = RunServoMovements(timer = (placetime/4.0), loc = 0.0)
        humanState("Moving")
        if deproach:
          amat = deproach.WorldPositionMatrix
          move(amat, Linear = True)

    if task[0] == "HumanProcess":
      fromcompname = task[1].strip()
      fromcomponent = app.findComponent(fromcompname)
      sig = fromcomponent.findBehaviour("TaskDone")
      ptime = float(task[2])
      taskname = task[3].strip()
      #toolname = task[4].strip()

      autoReserveSelf(fromcomponent)

      if True: #had tool usage here
        wpm2 = fromcomponent.WorldPositionMatrix

        approach = None
        deproach = None
        for cc in fromcomponent.ChildComponents:
          if cc.getProperty("ID") and (cc.ID == "Approach" or cc.ID == "Both"):
            approach = cc
          if cc.getProperty("ID") and (cc.ID == "Deproach" or cc.ID == "Both"):
            deproach = cc

        for i in locs:
          if i.getProperty('ProcessTasks') and taskname in i.ProcessTasks.split(","):
            wpm2 = i.WorldPositionMatrix

        if wpm2 == fromcomponent.WorldPositionMatrix:
          in_node, res_loc_frame = findFeature(fromcomponent,'ResourceLocation')
          if res_loc_frame:
            wpm2 = in_node.WorldPositionMatrix * res_loc_frame.NodePositionMatrix

        humanState("Moving")

        dd = wpm2.P - fromcomponent.WorldPositionMatrix.P

        if approach:
          amat = approach.WorldPositionMatrix
          move(amat)

        LinearVal = False
        ClosestVal = None
        ttarget = wpm2
        if approach: LinearVal = True
        fromCompResLoc = fromcomponent.getProperty('ResourceLocation::ResourceLocation')
        if fromCompResLoc and fromCompResLoc.Value == 'NearestGrid':
          ClosestVal = True
          ttarget = fromcomponent.WorldPositionMatrix
        move(ttarget, Closest = ClosestVal, Linear = LinearVal)


        delay(ptime)
        sig.signal("&".join(task)) #reply that task is done
        humanState("Moving")
        if deproach:
          amat = deproach.WorldPositionMatrix
          move(amat, Linear = True)

    if task[0] == "Feed": #raw feed, only pick
      compname = task[2].strip() #Prod_ID
#      taskname = task[1].strip()
      toolname = task[4].strip()
      fromname = task[1].strip()

      fromcomponent = app.findComponent(fromname)
      fromCompResLoc = fromcomponent.getProperty('ResourceLocation::ResourceLocation')
      if fromCompResLoc and fromCompResLoc.Value == 'RelativeToProduct':
        print 'Warning in resource',comp.Name, 'pick RelativeToProduct not available in route control'

      approach = None
      deproach = None
      for cc in fromcomponent.ChildComponents:
        if cc.getProperty("ID") and (cc.ID == "Approach" or cc.ID == "Both"):
          approach = cc
        if cc.getProperty("ID") and (cc.ID == "Deproach" or cc.ID == "Both"):
          deproach = cc

      wpm2 = fromcomponent.WorldPositionMatrix
      prop = fromcomponent.getProperty("Advanced::PickCycleTime")
      picktime = prop.Value

      autoReserveSelf(fromcomponent)

      Loadtime = comp.getProperty("AGV::LoadingTime")
      if Loadtime.Value > 0: picktime = Loadtime.Value

      sensorcomponent = fromcomponent.getProperty("sensorcomponent")
      if sensorcomponent:
        contained = [x for x in fromcomponent.ChildComponents if x.Container] + [x for x in [sensorcomponent.Value] if x != None]
      else:
        contained = [x for x in fromcomponent.ChildComponents if x.Container]
      for c in contained: #returns the first
        id_prop = c.getProperty('ProdID')
        res = c.getProperty("resource_reserver")
        if id_prop and id_prop.Value == compname:
          if not res or (res and res.Value == ""):
            if not res: c.createProperty(VC_STRING, "resource_reserver")
            c.resource_reserver = comp.Name
            ccomp = c
            break

      if ccomp:
        if wpm2 == fromcomponent.WorldPositionMatrix:
          in_node, res_loc_frame = findFeature(fromcomponent,'ResourceLocation')
          if res_loc_frame:
            wpm2 = in_node.WorldPositionMatrix * res_loc_frame.NodePositionMatrix

        humanState("Moving")

        dd = wpm2.P - fromcomponent.WorldPositionMatrix.P

        if approach:
          amat = approach.WorldPositionMatrix
          move(amat)
        LinearVal = False
        ClosestVal = None
        ttarget = wpm2
        if approach: LinearVal = True
        fromCompResLoc = fromcomponent.getProperty('ResourceLocation::ResourceLocation')
        if fromCompResLoc and fromCompResLoc.Value == 'NearestGrid':
          ClosestVal = True
          ttarget = ccomp.WorldPositionMatrix
        move(ttarget, Closest = ClosestVal, Linear = LinearVal)

        humanState("Picking")
        returntime = RunServoMovements(matrix = ccomp.WorldPositionMatrix, timer = (picktime/4.0))
        if picktime <= 0: picktime = 1.0

        Pick(ccomp, timer = picktime-(returntime*2.0))

        humanState("Moving")
        if deproach:
          amat = deproach.WorldPositionMatrix
          move(amat, Linear = True)
        returntime = RunServoMovements(timer = (picktime/4.0), loc = 0.0)
      else:
        comp.Busy = False
        if not queue2:
          delay(1.0)
        else:
          delay(0.1)
        queue2.append(task)

    elif task[0] == "Transport":
      compname = task[4].strip() #Prod_ID
      taskname = task[1].strip()
      toolname = task[6].strip()
      tcpname = task[7].strip()
      fromname = task[2].strip()

      fromcomponent = app.findComponent(fromname)

      autoReserveSelf(fromcomponent)

      approach = None
      deproach = None
      for cc in fromcomponent.ChildComponents:
        if cc.getProperty("ID") and (cc.ID == "Approach" or cc.ID == "Both"):
          approach = cc
        if cc.getProperty("ID") and (cc.ID == "Deproach" or cc.ID == "Both"):
          deproach = cc

      ccomp = None
      if fromcomponent.getProperty("sensorcomponent"):
        contained = [x for x in fromcomponent.ChildComponents if x.Container] + [x for x in [fromcomponent.sensorcomponent] if x != None]
      else:
        contained = [x for x in fromcomponent.ChildComponents if x.Container]
      for c in contained: #returns the first
        id_prop = c.getProperty('ProdID')
        res = c.getProperty("resource_reserver")
        if id_prop and id_prop.Value == compname:
          if not res or (res and res.Value == ""):
            if not res: c.createProperty(VC_STRING, "resource_reserver")
            c.resource_reserver = comp.Name
            ccomp = c
            break

      if ccomp:
        wpm2 = fromcomponent.WorldPositionMatrix
        prop = fromcomponent.getProperty("Advanced::PickCycleTime")
        picktime = prop.Value

        fromLocFound = False
        for i in locs:
          if i.getProperty('PickTasks') and taskname in i.PickTasks.split(","):
            wpm2 = i.WorldPositionMatrix
            fromLocFound = True
            if i.PickDelay > 0: picktime = i.PickDelay

        fromCompResLoc = fromcomponent.getProperty('ResourceLocation::ResourceLocation')
        if fromLocFound == False and fromCompResLoc:
          if fromCompResLoc.Value == 'RelativeToProduct':
            relMtx = fromcomponent.InverseWorldPositionMatrix * fromcomponent.WorldPositionMatrix* fromcomponent.findFeature('ResourceLocation').NodePositionMatrix
            wpm2 = ccomp.WorldPositionMatrix*relMtx
            p = wpm2.P
            p.Z = 0
            wpm2.P = p

        Loadtime = comp.getProperty("AGV::LoadingTime")
        if Loadtime.Value > 0: picktime = Loadtime.Value

        if wpm2 == fromcomponent.WorldPositionMatrix:
          in_node, res_loc_frame = findFeature(fromcomponent,'ResourceLocation')
          if res_loc_frame:
            wpm2 = in_node.WorldPositionMatrix * res_loc_frame.NodePositionMatrix
        humanState("Moving")

        dd = wpm2.P - fromcomponent.WorldPositionMatrix.P

        if approach:
          amat = approach.WorldPositionMatrix
          move(amat)
        
        LinearVal = False
        ClosestVal = None
        ttarget = wpm2
        if approach: LinearVal = True
        fromCompResLoc = fromcomponent.getProperty('ResourceLocation::ResourceLocation')
        if fromCompResLoc and fromCompResLoc.Value == 'NearestGrid':
          ClosestVal = True
          ttarget = ccomp.WorldPositionMatrix
        move(ttarget, Closest = ClosestVal, Linear = LinearVal)

        humanState("Picking")
        returntime = RunServoMovements(matrix = ccomp.WorldPositionMatrix, timer = (picktime/4.0))
        if picktime <= 0: picktime = 1.0

        Pick(ccomp, tooldata = (toolname, tcpname), timer = picktime-(returntime*2.0))

        humanState("Moving")

        if deproach:
          amat = deproach.WorldPositionMatrix
          move(amat, Linear = True)
        returntime = RunServoMovements(timer = (picktime/4.0), loc = 0.0)

        if ccomp:
          toname = task[3].strip()
          toloc = task[5]
          tocomponent = app.findComponent(toname)
          tocont = tocomponent.findBehaviour("Container__HIDE__")

          autoReserveSelf(tocomponent)

          approach = None
          deproach = None
          for cc in tocomponent.ChildComponents:
            if cc.getProperty("ID") and (cc.ID == "Approach" or cc.ID == "Both"):
              approach = cc
            if cc.getProperty("ID") and (cc.ID == "Deproach" or cc.ID == "Both"):
              deproach = cc

          prop = tocomponent.getProperty("Advanced::PlaceCycleTime")
          placetime = prop.Value

          Unloadtime = comp.getProperty("AGV::UnloadingTime")
          if Unloadtime.Value > 0: placetime = Unloadtime.Value

          wpm2 = tocomponent.WorldPositionMatrix
          toLocFound = False
          for i in locs:
            if i.getProperty('PlaceTasks') and taskname in i.PlaceTasks.split(","):
              wpm2 = i.WorldPositionMatrix
              toLocFound = True
              if i.PlaceDelay > 0: placetime = i.PlaceDelay

          if wpm2 == tocomponent.WorldPositionMatrix:
            in_node, res_loc_frame = findFeature(tocomponent,'ResourceLocation') #approach frame, closest cell
            if res_loc_frame:
              wpm2 = in_node.WorldPositionMatrix * res_loc_frame.NodePositionMatrix

          posname = "MW_%s"%(compname)
          count_of_this_id = len( [x for x in tocomponent.ChildComponents if x.Container and x.getProperty('ProdID') and x.getProperty('ProdID').Value.strip() == compname.strip()] )
          frame_with_index = tocomponent.getFeature( '%s_%i' % ( posname , count_of_this_id )  )
          if frame_with_index:
            m1 = frame_with_index.NodePositionMatrix
            target = m1
          else:
            frame_without_index = tocomponent.getFeature(posname)
            if frame_without_index:
              m1 = frame_without_index.NodePositionMatrix
              target = m1
            else:
              tocomponent.createProperty(VC_MATRIX, 'DefaultMatrix')
              target = tocomponent.DefaultMatrix #default location

          if tocomponent.getProperty("sensorcomponent"):
            ccomps = tocont.Components + [x for x in [tocomponent.sensorcomponent] if x != None]
          else: ccomps = tocont.Components
          for cc in ccomps:
            if cc.getProperty("ChildrenInAssembly") and cc.ChildrenInAssembly == ccomp.Name:
              if ccomp.getProperty("LocationInAssembly"):
                target = cc.PositionMatrix * ccomp.LocationInAssembly

          poses = toloc.split(",")
          target.translateRel(float(poses[0]),float(poses[1]),float(poses[2]))
          if len(poses) > 3:
            target.rotateRelZ(float(poses[3]))
            target.rotateRelY(float(poses[4]))
            target.rotateRelX(float(poses[5]))

          mat = tocont.Parent.WorldPositionMatrix * target

          humanState("Transporting")

          dd = wpm2.P - tocomponent.WorldPositionMatrix.P

          toCompResLoc = tocomponent.getProperty('ResourceLocation::ResourceLocation')
          if toLocFound == False and toCompResLoc:
            if toCompResLoc.Value == 'RelativeToProduct':
              resLocWorldMtx = tocomponent.WorldPositionMatrix*tocomponent.findFeature('ResourceLocation').NodePositionMatrix
              relMtx = tocomponent.InverseWorldPositionMatrix * resLocWorldMtx
              wpm2 = tocomponent.WorldPositionMatrix*target*relMtx
              p = wpm2.P
              p.Z = resLocWorldMtx.P.Z
              wpm2.P = p


          if approach:
            amat = approach.WorldPositionMatrix
            move(amat)
          returntime = RunServoMovements(matrix = mat, timer = (placetime/4.0))
          LinearVal = False
          ClosestVal = None
          ttarget = wpm2
          if approach: LinearVal = True
          toCompResLoc = tocomponent.getProperty('ResourceLocation::ResourceLocation')
          if toCompResLoc and toCompResLoc.Value == 'NearestGrid':
            ClosestVal = True
            ttarget = mat
          move(ttarget, Closest = ClosestVal, Linear = LinearVal)


          humanState("Placing")
          if placetime <= 0: placetime = 1.0
          Place(ccomp,mat,tocomponent, timer = placetime - (returntime * 2))

          tocont.grab(ccomp)
          keepProdOrientation = tocomponent.getProperty('Advanced::KeepProdOrientation')
          if not keepProdOrientation or (keepProdOrientation and not keepProdOrientation.Value):
            ccomp.PositionMatrix = target
            ccomp.update()

          picked -= 1

          res = ccomp.getProperty("resource_reserver")
          if res:
            ccomp.resource_reserver = ""
          returntime = RunServoMovements(timer = (placetime/4.0), loc = 0.0)
          humanState("Moving")
          if deproach:
            amat = deproach.WorldPositionMatrix
            move(amat, Linear = True)
          

      else: #this should newer happen
        print "Tranport task with no component in line 721"
        comp.Busy = False
        if not queue2:
          delay(1.0)
        else:
          delay(0.1)
        queue2.append(task)
    previoustask = task[0]


def OnReset():
  ROOT.UserGeometry.clear()

  comp.RunningRoute = ""

def humanState(state): #states so far, LaborProcessing
  global bnode
  global stats
  global chargepositions, p_currentcapacity, p_capacity, p_busy, p_idle, p_rate, p_filter
  global timestamp
  stats.State = state
  spenttime = sim.SimTime - timestamp
  if state == "Idle" or state == "Picking" or state == "Placing" or state == "Blocked" or state == "Break":
    p_currentcapacity.Value -= ((spenttime/3600.0) * p_idle)
  else:
    p_currentcapacity.Value -= ((spenttime/3600.0) * p_busy)
  timestamp = sim.SimTime
  if bnode:
    if state == "Processing" or state == "Repairing" or state == "Assisting" or state == "Transporting" or state == "Picking" or state == "Placing":
      bnode.NodeMaterial = app.findMaterial("green")
    if state == "Idle" or state == "Moving" or state == "CollectingTool" or state == "ReturningTool":
      bnode.NodeMaterial = app.findMaterial("yellow")
    if state == "Bypassing" or state == "Blocked":
      bnode.NodeMaterial = app.findMaterial("orange")
    if state == "Break":
      bnode.NodeMaterial = app.findMaterial("red")
    if state == "Charging":
      bnode.NodeMaterial = app.findMaterial("red")

def returnfeatureworldlocation(t, feature): #ADD CHECK IF FRAMES IN LONGER NODE TREE
  t.update()
  f = t.getFeature(feature)
  if f:
    npos = f.NodePositionMatrix
    tpos = t.WorldPositionMatrix
    wpos = tpos * npos
    return wpos, npos
  else:
#    print "not found: " + feature
    return None, None

def findFeature(fromcomponent, f_name):
  nodes = [fromcomponent]
  if fromcomponent.Parent.Name != 'ROOT':
    nodes.insert(0,fromcomponent.Parent.Component)
  while nodes:
    node = nodes.pop(0)
    nodes.extend([x for x in node.Children if x.Component == fromcomponent] )
    feat = node.getFeature(f_name)
    if feat:
      return node, feat
  return None, None



######################################################
##  PRODUCT HANDLING
######################################################

def Place(part, target_wpm, tocomponent, timer = 1.0):
  global iterspersec
  global PatternSlots, PatternLocations
  global tuglist, tugcomps
  global cartcomps
  
  if timer < 1.0: timer = 1.0
  stats.flowLeave(part)
  gonetocart = False
  for cart in cartcomps:
    cont = cart.findBehaviour("TargetContainer__HIDE__")
    if part in cont.Components:
      gonetocart = True
      prop = cart.getProperty("AGV::UnloadingTime")
      timer = prop.Value

  if part not in tugcomps and not gonetocart:
    ind = PatternSlots.index(part)
    PatternSlots[ind] = None

  if robo and part not in tugcomps and not gonetocart: #if we have robot arm and component is onboard (not tugged nor in cart)
    robo.Configuration = robot_config_prop.Value
    robo.jointMoveToMtx(part.WorldPositionMatrix, Tz = part.BoundDiagonal.Z*2.0 + 50.0, Rx = 180.0)
    robo.jointMoveToMtx(part.WorldPositionMatrix, Tz = part.BoundDiagonal.Z*2.0, Rx = 180.0)
    robo.graspComponent(part)
    robo.jointMoveToMtx(part.WorldPositionMatrix, Tz = part.BoundDiagonal.Z*2.0 + 50.0, Rx = 180.0)
    robo.jointMoveToMtx(target_wpm, Tz = part.BoundDiagonal.Z*2.0+50.0, Rx = 180.0)
    robo.jointMoveToMtx(target_wpm, Tz = part.BoundDiagonal.Z*2.0, Rx = 180.0)
    tcont.grab(part)
    robo.jointMoveToMtx(target_wpm, Tz = part.BoundDiagonal.Z*2.0+50.0, Rx = 180.0)
    robo.driveJoints(*robot_home_joint_vals)
  else:
    if tocomponent.getProperty('Advanced::KeepProdOrientation') and tocomponent.getProperty('Advanced::KeepProdOrientation').Value == True:
      m = vcMatrix.new()
      m.translateRel(target_wpm.P.X,target_wpm.P.Y,target_wpm.P.Z)
      m.rotateRelX(part.WorldPositionMatrix.WPR.X)
      m.rotateRelY(part.WorldPositionMatrix.WPR.Y)
      m.rotateRelZ(part.WorldPositionMatrix.WPR.Z)
      target_wpm = m
    interpolateobject(part, target_wpm, timer)# * 0.3) #
  if part in tugcomps: tugcomps.remove(part)

def Pick(target, tooldata = (None, None), timer = 1.0): #part we want to pick as object handle
  global iterspersec
  global tcont
  global PatternSlots, PatternLocations
  global tuglist, tugcomps
  global cartcomps
  global stats
  
  if timer < 1.0: timer = 1.0
  stats.flowEnter(target)
  gonetocart = False
  for cart in cartcomps:
    idlist = [x.strip() for x in cart.ProdIDList.split(",")]
    cont = cart.findBehaviour("TargetContainer__HIDE__")
    cPattern = cart.getProperty("AGV::Pattern")
    cPatternAmount = int(cPattern.Value.X) * int(cPattern.Value.Y) * int(cPattern.Value.Z)

    if target.ProdID in idlist and not gonetocart and cont.ComponentCount < cPatternAmount:
      gonetocart = True
      ltime = cart.getProperty("AGV::LoadingTime")
      ltime.Value = timer
      cont.grab(target)
      delay(timer)

  if gonetocart == False:
    if target.ProdID not in tuglist:
      locs_node = comp.findNode("LocCont") #human chest mode
      name = target.Name
      if target.getProperty("ProdID"):
        name = target.ProdID
      name += "_location"
      target_wpm, _node = returnfeatureworldlocation(locs_node, name) #check do we have teached location
       #no teached location!
      ind = PatternSlots.index(None)

      if not target_wpm:
        if target.Parent.getProperty('Advanced::KeepProdOrientation') and target.Parent.getProperty('Advanced::KeepProdOrientation').Value == True:
          target_wpm = locs_node.WorldPositionMatrix * PatternLocations[ind]
          m = vcMatrix.new()
          m.translateRel(target_wpm.P.X,target_wpm.P.Y,target_wpm.P.Z)
          m.rotateRelX(target.WorldPositionMatrix.WPR.X)
          m.rotateRelY(target.WorldPositionMatrix.WPR.Y)
          m.rotateRelZ(target.WorldPositionMatrix.WPR.Z)
          target_wpm = m
        else:
          target_wpm = locs_node.WorldPositionMatrix * PatternLocations[ind]

      PatternSlots[ind] = target
      if robo:
        toolOK = doEoatChange(robo, robot_tool_iface, *tooldata)
        robo.Configuration = robot_config_prop.Value
        robo.jointMoveToMtx(target.WorldPositionMatrix, Tz = target.BoundDiagonal.Z*2.0 + 50.0, Rx = 180.0)
        robo.jointMoveToMtx(target.WorldPositionMatrix, Tz = target.BoundDiagonal.Z*2.0, Rx = 180.0)
        robo.graspComponent(target)
        robo.jointMoveToMtx(target.WorldPositionMatrix, Tz = target.BoundDiagonal.Z*2.0 + 50.0, Rx = 180.0)
        robo.jointMoveToMtx(target_wpm, Tz = target.BoundDiagonal.Z*2.0+50.0, Rx = 180.0)
        robo.jointMoveToMtx(target_wpm, Tz = target.BoundDiagonal.Z*2.0, Rx = 180.0)
        tcont.grab(target)
        robo.jointMoveToMtx(target_wpm, Tz = target.BoundDiagonal.Z*2.0+50.0, Rx = 180.0)
        robo.driveJoints(*robot_home_joint_vals)
      else:
        interpolateobject(target, target_wpm, timer)

    tcont.grab(target)
    if target.ProdID in tuglist:
      tugcomps.append(target)

def interpolateobject(obj, targetM, time): #object handle to target loc
  global iterspersec, updatetime, oldtime, opt_animate
  iters = int(time * iterspersec)
  if iters == 0: iters = 1
  dtime = time / iters

  pos_m = world.InverseWorldPositionMatrix * obj.WorldPositionMatrix
  world.attach(obj, False)
  obj.PositionMatrix = pos_m

  for t in range(iters):
    if updatetime != oldtime or t >= int(iters)-1:
      oldtime = updatetime
      ret = matInterpolator((t+1.0)/iters,pos_m,targetM)
      obj.PositionMatrix = ret
    delay(dtime)



######################################################
##  AGV MOVEMENT
######################################################

def move(ToLocation, Closest = None, Linear = False): #Interpolation can be Linear, TurnLinear, Hermite
  global controller
  global updatetime, oldtime
  global stats
  global avoid_comps
  global currentarea
  global opt_pathfinding,opt_avoidance
  global ReservedCells
  global tugcomps, cartcomps, tcont

  curstate = stats.State
  comp.update()
  wpr = comp.WorldPositionMatrix
  From = wpr.P

  if comp.getProperty('AGV::LocationOffset'):
    locationOffset = comp.getProperty('AGV::LocationOffset')
    ToLocation.translateRel(-locationOffset.Value,0,0)

  To = ToLocation.P

  dist = abs( (From-To).length() )
  # If already at the target
  if dist < 10:
    return

  comp.Path_Points = ""
  wayPointVectors = [ wpr.P ]

  if not controller or opt_pathfinding.Value == False: #we move linearly to target

    move_speed = comp.MoveSpeed
#    if comp.ToolSpeed != 0: move_speed = comp.ToolSpeed
    wayPointVectors.append(To)


    if Linear:
      standmove = True
      move_speed = comp.MoveSpeedApproach
    else: standmove = False
    move_length = MoveOneCell(0, To, wayPointVectors, False, move_speed, stair = False, stand = standmove) #linear interpolation directly to To location

    walk_dist_prop.Value += move_length

  else:
    #request path from pathway controller
    reqsignal = controller.findBehaviour("RoutingRequest")
    data = "Path" + "," + comp.Name+ "," +str(From.X) + ":" + str(From.Y)  + ":" + str(From.Z)+ "," + str(To.X) + ":" + str(To.Y) + ":" + str(To.Z)
    comp.ReplyController = False
    reqsignal.signal(data)
    delay(0.0)
    while comp.ReplyController == False:
      delay(0.01)
    while comp.Path_Points == "":
      delay(0.1)
    route = [x.split(',') for x in comp.Path_Points.split(":")] #<reply from controller, list of vectors
    waypointCells = [x for x in comp.Path_Comps.split(":")] #<reply from controller, list of cell names. Cell name include also component name "Component name && CellName"
    skiplist = [int(x) for x in comp.Path_Skip.split(":")] #<reply from controller, list of data are we allowed to skip a cell (to make smoother movement)
    wayPointVectors = [vcVector.new(float(x[0]), float(x[1]), float(x[2])) for x in route]
    waypointCells.append(waypointCells[-1])

    ##code to remove unnecessary steps in lifter
    removeind = []
    for w in range(len(waypointCells)):
      if w > 0 and w < len(waypointCells)-1:
        pre = waypointCells[w-1].split("&&")[0]
        cur = waypointCells[w].split("&&")[0]
        nex = waypointCells[w+1].split("&&")[0]
        precell = app.findComponent(pre)
        curcell = app.findComponent(cur)
        nexcell = app.findComponent(nex)
        if precell.ID == "PathwaySystem_Lifter" and curcell.ID == "PathwaySystem_Lifter" and nexcell.ID == "PathwaySystem_Lifter":
          removeind.append(w)
    for r in removeind:
      waypointCells.pop(r)
      wayPointVectors.pop(r)

    if Closest == None or Linear == True: #one step distance, if closest is none, remove last step and replace it with actual movement target
      wayPointVectors.pop(-1) #remove last cell
      wayPointVectors.append(To) #add actual target
#    if len(wayPointVectors)<2 and Closest == None: #also if no controller....
#      wayPointVectors.append(To)

    skips = []
    skipped = False
    forcecurrent = None

    for i, vec in enumerate(wayPointVectors[1:]):
      if not ReservedCells: currentcell = waypointCells[i]
      else: currentcell = ReservedCells[-1]
      for c in ReservedCells: #check that we have only one cell reserved. CHANGE THIS TO REFLECT TRAIN / TUG RESERVATION
        if c != currentcell:
          print "removing duplicate cells: Recovered error"
          reqsignal.signal( ",".join(("Release", comp.Name, c)) )
          delay(0.0)
          while comp.ReplyController == False:
            delay(0.01)
      targetcell = waypointCells[i+1]

      currentcellname = currentcell.split("&&")[0]
      nextcellname = targetcell.split("&&")[0]

      nextcell = app.findComponent(nextcellname)
      curcell = app.findComponent(currentcellname)
      stairs = False
      lift = False
      if nextcell.ID == "PathwaySystem_Stairs": stairs = True
      if nextcell.ID == "PathwaySystem_Lifter": lift = True

      comp.Reply = 0
      c_wpr = comp.WorldPositionMatrix

      can_skip = False #check can we skip a cell to create more smooth movement
      if not skipped and skiplist[i] == 0 and targetcell != waypointCells[-1]:
        nextmovedistance = (vec-c_wpr.P).length()
        can_skip = True
        if opt_avoidance.Value:
          for ac in avoid_comps: #check that no other components nearby
            ac_wpr = ac.WorldPositionMatrix
            ac_distance = (ac_wpr.P-c_wpr.P).length()
            if ac_distance < nextmovedistance*6.0:
              can_skip = False
      if skipped == False and can_skip:# and targetcell != waypointCells[-1] and not stairs and not lift:
        comp.ReplyController = False
        reqsignal.signal( ",".join(("Reserve", comp.Name, targetcell)) ) #reserve and release skipped cell anyway to update statistics
        delay(0.0)
        while comp.ReplyController == False:
          delay(0.01)
        comp.ReplyController = False
        reqsignal.signal( ",".join(("Release", comp.Name, targetcell)) ) #reserve and release skipped cell anyway to update statistics
        delay(0.0)
        while comp.ReplyController == False:
          delay(0.01)
        skips.append(targetcell)
        skipped = True
      else:
        skipped = False

      if opt_avoidance.Value:
        while comp.Reply != 1 and targetcell not in skips: #Reply 0 == waiting answer, reply 1 == ok, reply 2 == bypass
          #we are bypassing
          comp.ReplyController = False
          reqsignal.signal( ",".join(("Request", comp.Name, targetcell, currentcell)) )
          delay(0.0)
          while comp.ReplyController == False:
            delay(0.01)
          if comp.Reply == 2:
            humanState("Bypassing")
            skips = [x for x in comp.Skipped.split(":")]
            route = [x.split(',') for x in comp.Bypass_Points.split(":")]
            b_waypointCells = [x for x in comp.Bypass_Comps.split(":")]
            b_wayPointVectors = [vcVector.new(float(x[0]), float(x[1]), float(x[2])) for x in route]

            lastcurrentcell = "foobar"
            for i_temp in range(len(b_waypointCells)-1): #starting position is twice for some reason
              if b_waypointCells[i_temp] == lastcurrentcell or b_waypointCells[i_temp] == b_waypointCells[i_temp+1]: pass
              else:
                lastcurrentcell = b_waypointCells[i_temp]
                targetcell_temp = b_waypointCells[i_temp+1]
                currentcell_temp = b_waypointCells[i_temp]
                vec_b = b_wayPointVectors[i_temp+1]

                ReservedCells.append(targetcell_temp)

                lastcell = False
                currentcellname = currentcell_temp.split("&&")[0]
                nextcellname = targetcell_temp.split("&&")[0]
                nextcell = app.findComponent(nextcellname)
                curcell = app.findComponent(currentcellname)
                stairs = False
                if nextcell.ID == "PathwaySystem_Stairs": stairs = True

                #check movement speed
                move_speed = comp.MoveSpeedBypassing
                if nextcell.getProperty("AreaSpeedLimit") and nextcell.AreaSpeedLimit < move_speed and nextcell.AreaSpeedLimit > 0.0:
                  move_speed = nextcell.AreaSpeedLimit
                if (tugcomps or cartcomps) and move_speed > comp.MoveSpeedTowing:
                  move_speed = comp.MoveSpeedTowing
                elif tcont.Components and move_speed > comp.MoveSpeedLoaded:
                  move_speed = comp.MoveSpeedLoaded

                #update and check area capacity
                updatecap = False
                if currentcellname != nextcellname:# or currentarea != nextcellname:
                  updatecap = True
                  if nextcell.getProperty("AreaCapacity") :
                    while nextcell.CurrentCapacity >= nextcell.AreaCapacity:
                      humanState("Blocked")
                      delay(1.0)
                    nextcell.CurrentCapacity+=1
                standingmove = False

                #check if next step is lifter and is lifter awailable, wait for lifter to arrive
                if nextcell.ID == "PathwaySystem_Lifter" and curcell.ID != "PathwaySystem_Lifter":
                  reqsig = nextcell.findBehaviour("reserve")
                  liftval = ":".join(( comp.Name, str(int(vec.Z)), str(int(b_wayPointVectors[i+2].Z)) ))

                  comp.CustomAction = "waiting"
                  reqsig.signal(liftval)
                  while comp.CustomAction != "enter": delay(0.1) #wait for lifter

                #if we are in a lifter and moving several floors
                if nextcell.ID == "PathwaySystem_Lifter" and curcell.ID == "PathwaySystem_Lifter":
#                  IterPos2(_stand, 0.1, 0)
                  comp.CustomAction = "waiting"
                  while comp.CustomAction != "move": delay(0.1)
                  standingmove = True #we dont move in lifter

                if Linear:
                  standingmove = True
                  move_speed = comp.MoveSpeedApproach
                move_length = MoveOneCell(i_temp, vec_b, b_wayPointVectors, lastcell, move_speed, stair = stairs, stand = standingmove) #move one cell

                comp.ReplyController = False
                reqsignal.signal( ",".join(("Release", comp.Name, currentcell_temp)) ) #releae previous cell
                delay(0.0)
                while comp.ReplyController == False:
                  delay(0.01)
                if currentcell_temp in ReservedCells: ReservedCells.remove(currentcell_temp)
                walk_dist_prop.Value += move_length

                if updatecap: #update capacity
                  if curcell.getProperty("AreaCapacity") :
                    curcell.CurrentCapacity-=1

            comp.Reply = 1
            forcecurrent = targetcell_temp
          if comp.Reply !=1:
            humanState("Blocked")
            delay(1.0)
      humanState(curstate)

      if targetcell not in skips: #If this step is not bypassed
        lastcell = False
        if targetcell == waypointCells[-1]:
          lastcell = True
        #check speed
        move_speed = comp.MoveSpeed
        if nextcell.getProperty("AreaSpeedLimit") and nextcell.AreaSpeedLimit < move_speed and nextcell.AreaSpeedLimit > 0.0:
          move_speed = nextcell.AreaSpeedLimit
        if (tugcomps or cartcomps) and move_speed > comp.MoveSpeedTowing:
          move_speed = comp.MoveSpeedTowing
        elif tcont.Components and move_speed > comp.MoveSpeedLoaded:
          move_speed = comp.MoveSpeedLoaded


        updatecap = False #update area capacity
        if currentcellname != nextcellname:# or currentarea != nextcellname:
          updatecap = True
          if nextcell.getProperty("AreaCapacity") :
            while nextcell.CurrentCapacity >= nextcell.AreaCapacity:
              humanState("Blocked")
              delay(1.0)
            nextcell.CurrentCapacity+=1

        if opt_avoidance.Value:
          comp.ReplyController = False
          reqsignal.signal( ",".join(("Reserve", comp.Name, targetcell)) ) #reserve next cell
          delay(0.0)
          while comp.ReplyController == False:
            delay(0.01)
          ReservedCells.append(targetcell)
        standingmove = False

        #check if next step is lifter and is lifter awailable, wait for lifter to arrive
        if nextcell.ID == "PathwaySystem_Lifter" and curcell.ID != "PathwaySystem_Lifter":
          reqsig = nextcell.findBehaviour("reserve")
          liftval = ":".join(( comp.Name, str(int(vec.Z)), str(int(wayPointVectors[i+2].Z)) ))
          comp.CustomAction = "waiting"
          reqsig.signal(liftval)
          delay(0.0)
          while comp.CustomAction != "enter": delay(0.1)

        #if we are in a lifter and moving several floors
        if nextcell.ID == "PathwaySystem_Lifter" and curcell.ID == "PathwaySystem_Lifter":
          comp.CustomAction = "waiting"
          delay(0.1)
          while comp.CustomAction != "move": delay(0.1)
          standingmove = True
        if Linear:
          standingmove = True
          move_speed = comp.MoveSpeedApproach
        move_length = MoveOneCell(i, vec, wayPointVectors, lastcell, move_speed, stair = stairs, stand = standingmove)

        walk_dist_prop.Value += move_length

        if updatecap:#update area capacity
          if curcell.getProperty("AreaCapacity") :
            curcell.CurrentCapacity-=1

        if opt_avoidance.Value:
          comp.ReplyController = False
          reqsignal.signal( ",".join(("Release", comp.Name, currentcell)) ) #release previous cell
          delay(0.0)
          while comp.ReplyController == False:
            delay(0.01)
          if currentcell in ReservedCells: ReservedCells.remove(currentcell)

  if Closest == None or Linear == True: #turn to matrix (like resource frame)
    if Linear and comp.WorldPositionMatrix != ToLocation:
      move_speed = comp.MoveSpeedApproach
      comp.update()
      c_wpr = comp.WorldPositionMatrix
      move_length = (c_wpr.P-ToLocation.P).length()
      move_time = move_length / move_speed
      IterPos2("foobar", move_time, 0, pelvistarget = ToLocation)
      walk_dist_prop.Value += move_length

    rot_t = vcMatrix.new(ToLocation)
    rot_t.translateRel(1000,0,0)
    turnangle = Turn(rot_t.P)
    turnme(turnangle)
  else: #If closest movement cell used, turn to face target location
    rot_t = vcMatrix.new(ToLocation)
    turnangle = Turn(rot_t.P)
    turnme(turnangle)

def MoveOneCell(i, vec, wayPointVectors, lastcell, move_speed, stair = False, stand = False): #vec = target
  global runningstep, driving, multi
  global _stand,_leftup,_rightup,_rightdown,_leftdown
  global opt_animate, opt_avoidance
  global tuglocations

  comp.update()
  c_wpr = comp.WorldPositionMatrix

  move_length = (c_wpr.P-vec).length()
  move_time = move_length / move_speed
  turnangle = Turn(vec) #check for angle

  targetmat = vcMatrix.new(comp.WorldPositionMatrix)
  currentmat = vcMatrix.new(comp.WorldPositionMatrix)
  targetmat.P = wayPointVectors[i+1]
  if not stand: targetmat.rotateRelZ(turnangle)

  IterPos2("foobar", move_time, 0, pelvistarget = targetmat) #direct iteration from position to position: ADD RESOURCE SPECIFIC MOVEMENT HERE
  return move_length

#V4.0
def turnme(turnangle): #Turn on spot
  global moves, runningstep, pars, updatetime, oldtime
  global _stand,_leftup,_rightup,_rightdown,_leftdown
  comp.update()
  timer = abs(turnangle) / comp.TurnSpeed
  if timer < 0.5: timer = 0.5 #IF we need to turn, minimum time is 0.5 anyway
#  IterPos2(_stand, timer*0.2, 0)
  currentmat = vcMatrix.new(comp.WorldPositionMatrix)
  currentmat.rotateRelZ(turnangle)
  IterPos2("foobar", timer * 1.0, 0, pelvistarget = currentmat, pelvisincrement = 1.0)

  comp.PositionMatrix = currentmat


#iterate component location
def IterPos2(data, time, frame, locks = [], pelvisheight = 0, pelvistarget = None, pelvisincrement = 1.0, lltarget = None, rltarget = None, lhtarget = None, rhtarget = None, fromline = 0): #single posture iteration, like "<stand>".
  global updatetime, oldtime, iterspersec
  global opt_animate, opt_avoidance
  global tuglist, tugcomps, tuglocations
  global cartlist, cartcomps
  iters = int(time * iterspersec)
  if iters == 0: iters = 1
  dtime = float(time) / float(iters)
  comp.update()
  invmat = vcMatrix.new(comp.InverseWorldPositionMatrix)
  currentmat = vcMatrix.new(comp.WorldPositionMatrix)


  for t in range(iters): #time to item = 1 sec, 25 frames
    if updatetime != oldtime or t >= int(iters)-1:   #update only if simulation world is updated
      oldtime = updatetime
      if pelvistarget != None:
        ret = matInterpolator(((t+1.0)/iters) * pelvisincrement,currentmat,pelvistarget)
        comp.PositionMatrix = ret
        checkdist = (tuglocations[-1].P - ret.P).length()
        if checkdist > 10.0: tuglocations.append(ret) #if component location id moved more that 10mm, add latest location to list
        if len(tuglocations) > 1000: tuglocations.pop(0) #if the are more than 1000 locations saved, remove oldest one
      ##tug component locations
        d = comp.AGVLength*.5
        if cartcomps: #check carts
          add = comp.getProperty("AGV::CartDistance")
          d+=add.Value
          for cart in cartcomps:
            d += cart.CartLength*.5
#            tlocs = tuglocations[:]
            travelleddistance = 0
            tlocs = tuglocations[:]
            tlocs.reverse()
            foundloc = False
            for v in range(len(tlocs)): #go through location list until we reach the distance of tugged part or cart
              if v == 0: lastdistance = (comp.WorldPositionMatrix.P - tlocs[v].P).length()
              else: lastdistance = (tlocs[v-1].P - tlocs[v].P).length()
#              print lastdistance
              travelleddistance += lastdistance
              if travelleddistance > d:
#                print travelleddistance
                cart.PositionMatrix = comp.InverseWorldPositionMatrix * tlocs[v]
                foundloc = True
#                location = tlocs[v]
                break
            if foundloc == False:
              cart.PositionMatrix = vcMatrix.new()
            d += cart.CartLength*.5 + add.Value

        if tugcomps:
          add = comp.getProperty("AGV::TugDistance")
          if not cartcomps: d += add.Value
          for tug in tugcomps:
            d += tug.BoundDiagonal.X
#            tlocs = tuglocations[:]
            travelleddistance = 0
            tlocs = tuglocations[:]
            tlocs.reverse()
            foundloc = False
            for v in range(len(tlocs)):
              if v == 0: lastdistance = (comp.WorldPositionMatrix.P - tlocs[v].P).length()
              else: lastdistance = (tlocs[v-1].P - tlocs[v].P).length()
#              print lastdistance
              travelleddistance += lastdistance
              if travelleddistance > d:
#                print travelleddistance
                tug.PositionMatrix = comp.InverseWorldPositionMatrix * tlocs[v]
                foundloc = True
#                location = tlocs[v]
                break
            if foundloc == False:
              tug.PositionMatrix = vcMatrix.new()
            d += tug.BoundDiagonal.X + add.Value
        comp.update()
    delay(dtime)

#V4.0
def Turn(vec): #returns degrees how much to turn to correct direction
  comp.update()
  v = vcVector.new(1,0,0)
  myloc = comp.WorldPositionMatrix
  start_dir = myloc.N
  myAngle = math.atan2( start_dir.Y, start_dir.X )*RAD_TO_DEG
  targetvec = vec - myloc.P
  targetAngle = math.atan2( targetvec.Y, targetvec.X )*RAD_TO_DEG
  turnangle = targetAngle - myAngle
  if turnangle > 180:
    turnangle = -(360-turnangle)
  if turnangle < -180:
    turnangle = 360+turnangle
  return turnangle



######################################################
##  ROBOT CONTROLS
######################################################

robot_config_prop = comp.getProperty('Robot::Configuration')
robot_flange_tcp_prop = comp.getProperty('Robot::FlangeTCP')
robot_default_tcp_prop = comp.getProperty('Robot::DefaultTCP')

def doEoatChange(robo, robot_tool_iface, eoat, tcp):
  # eoat ~ End Of Arm Tool
  #global statistics
  #state = statistics.State
  #statistics.State = 'ToolChanging'
  #eoat = eoat.strip()

  if not eoat:
    # no eoat given, continue with default tcp and current eoat => all fine
    #statistics.State = state
    default_tcp = robot_default_tcp_prop.Value
    setTcp(robo, default_tcp)
    return True
  elif eoat == '-':
    ##
    ## task doesn't require an eoat
    if robot_tool_iface.ConnectedComponent:
      # robot is holding an eoat => return the eoat
      returnEOAT(robo, robot_tool_iface)
      #statistics.State = state
      return True
    else:
      # robot is not holding an eoat and  => all fine
      #statistics.State = state
      flange_tcp = robot_flange_tcp_prop.Value
      setTcp(robo, flange_tcp)
      return True
  elif eoat:
    ##
    ## task requires an eoat
    if robot_tool_iface.ConnectedComponent and robot_tool_iface.ConnectedComponent.Name == eoat:
      # robot is holding the correct eoat => all fine
      #statistics.State = state
      setTcp(robo, tcp)
      return True
    if robot_tool_iface.ConnectedComponent and robot_tool_iface.ConnectedComponent.Name != eoat:
      # robot is holding a wrong eoat => return the eoat
      returnEOAT(robo, robot_tool_iface)
    eoat_comp = app.findComponent(eoat)
    if eoat_comp:
      # correct eoat exists => get it
      getEOAT(robo, robot_tool_iface, eoat_comp)
      setTcp(robo, tcp)
      #statistics.State = state
      return True
    else:
      # correct eoat doesn't exist in the layout => return false from this function
      print '{}: No EndOfArmTool -component named "{}" found in the layout.'.format(comp.Name, eoat)
      #statistics.State = state
      flange_tcp = robot_flange_tcp_prop.Value
      setTcp(robo, flange_tcp)
      return False
  #statistics.State = state # just in case

testerINT = lambda x,y,tcp: x == tcp-1
testerSTR = lambda x,y,tcp: y.Name == tcp
def setTcp(robo, tcp):
  try:
    tcp = int(tcp)
    tester = testerINT
  except:
    tcp = tcp
    tester = testerSTR
  for i,t in enumerate(robo.Controller.Tools):
    if tester(i,t,tcp):
      break
  else:
    tcp = ''
  robo.ActiveTool = tcp

def returnEOAT(robo, robot_tool_iface):
  #global statistics
  #state = statistics.State
  #statistics.State = 'ToolChanging'
  curtool = robot_tool_iface.ConnectedComponent
  parent, tar_pos = eoat_init_location( curtool, store=False )
  flange_tcp = robot_flange_tcp_prop.Value
  setTcp(robo, flange_tcp)
  #if robot_config_prop.Value in robo.ConfigurationsList:
  robo.Configuration = robot_config_prop.Value
  if parent.Parent: # not the World
    parent.update()
  robo.jointMoveToMtx(parent.WorldPositionMatrix * tar_pos)
  robo.Component.update()
  t_ints = curtool.findBehavioursByType(VC_ONETOONEINTERFACE)
  t_int = t_ints[0]
  robot_tool_iface.unConnect(t_int)
  parent.attach(curtool, True)
  curtool.PositionMatrix = tar_pos
  curtool.update()
  curtool.saveState()
  #statistics.State = state

def getEOAT(robo, robot_tool_iface, eoat_comp):
  global statistics
  #state = statistics.State
  #statistics.State = 'ToolChanging'
  parent, tar_pos = eoat_init_location( eoat_comp )
  flange_tcp = robot_flange_tcp_prop.Value
  setTcp(robo, flange_tcp)
  #if robot_config_prop.Value in robo.ConfigurationsList:
  robo.Configuration = robot_config_prop.Value
  if parent.Parent: # not the World
    parent.update()
  robo.jointMoveToMtx(parent.WorldPositionMatrix * tar_pos)
  robo.Component.update()
  t_ints = eoat_comp.findBehavioursByType(VC_ONETOONEINTERFACE)
  t_int = t_ints[0]
  robot_tool_iface.connect(t_int)
  #statistics.State = state

robot_tool_store_locs = {}
def eoat_init_location(eoat, store=True):
  if store:
    robot_tool_store_locs[eoat.Name] = (eoat.Parent, eoat.PositionMatrix)

  if eoat.Name in robot_tool_store_locs:
    return robot_tool_store_locs[eoat.Name]

  print "{} - ERROR: No defined place (pickup location) to return tool '{}'. Please, have all tools detached in the beginning of simulation.".format(comp.Name, eoat.Name)
  suspendRun()

def updateRobotConfigurationPropList(robot):
  # If not Robot2 instance (in case of OnAttach event)
  if type(robot) is not vcHelpers.Robot2.vcRobot2:
    try:
      robot = getRobot(robot)
    except AttributeError:
      pass # Because of OnAttach event when loading layout/comp (before Onfinalize)
    except Exception as e:
      raise(e)

  if robot_config_prop.Value not in robot.ConfigurationsList:
    robot_config_prop.StepValues = robot.ConfigurationsList
    robot_config_prop.Value = robot.ConfigurationsList[robotDefaultConfigIndex(robot)]

def robotDefaultConfigIndex(robot):
  if robot.Component.Name.startswith("UR"):
    return 1
  return 0

def showHideRobotTab(robot=None):
  val = True if robot else False
  robotProps = (p for p in comp.Properties if p.Name.startswith("Robot::"))
  for prop in robotProps:
    prop.IsVisible = val

def OnAttach(type, node1, node2):
  if not finalized:
    return
  if type == VC_NODE_ADD_FIRST_CHILD:
    if node1.findBehavioursByType(VC_ROBOTCONTROLLER):
      showHideRobotTab(node1)
      updateRobotConfigurationPropList(node1)

comp.OnNodeConfigurationChange = OnAttach


######################################################
##  RESERVING
######################################################

def autoReserveSelf(targetComp):
  targetCompAutoReserve = targetComp.getProperty("AutoReserveNextResource")
  targetCompReserved = targetComp.getProperty("AutoReservedResource")

  if not targetCompAutoReserve or not targetCompReserved:
    print "Update Works process '{}' in order to use ResourceReserve Task in ReserveFirst mode".format(targetComp.Name)
    return False

  if targetCompAutoReserve.Value:
    targetCompReserved.Value = comp.Name
    targetCompAutoReserve.Value = False
    comp.reserver = targetComp.Name
    return True

  return False


this_script = comp.findBehaviour('WorksBrains')

t_prop = comp.getProperty('AGV::TeachCarryLocation')
t_prop.OnChanged = teachcarry
t_prop2 = comp.getProperty('AGV::ClearCarryLocations')
t_prop2.OnChanged = clearcarry

servo_lift = comp.findBehaviour('Lift Controller')
if servo_lift:
  if servo_lift.Joints:
    lift_min_limit = float(servo_lift.Joints[0].MinValue)
    lift_max_limit = float(servo_lift.Joints[0].MaxValue)